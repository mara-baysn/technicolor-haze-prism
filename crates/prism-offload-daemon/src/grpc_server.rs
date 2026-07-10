use std::sync::Arc;
use std::time::Instant;

use tonic::{Request, Response, Status};

use prism_proto::session_offload_service_server::SessionOffloadService;
use prism_proto::{
    flush_sessions_request::MatchCriteria, FlushSessionsRequest, FlushSessionsResponse,
    HealthCheckRequest, HealthCheckResponse, ProgramSessionRequest, ProgramSessionResponse,
    QuerySessionRequest, QuerySessionResponse, SessionKey,
};

use crate::doca_flow::DocaFlowManager;
use crate::session_table::{SessionEntry, SessionTable};

/// gRPC service implementation for session offload operations.
pub struct OffloadServiceImpl {
    session_table: Arc<SessionTable>,
    doca_manager: Arc<DocaFlowManager>,
    start_time: Instant,
}

impl OffloadServiceImpl {
    pub fn new(session_table: Arc<SessionTable>, doca_manager: Arc<DocaFlowManager>) -> Self {
        Self {
            session_table,
            doca_manager,
            start_time: Instant::now(),
        }
    }
}

/// Build the reverse key for bidirectional sessions (swap src/dst).
fn reverse_key(key: &SessionKey) -> SessionKey {
    SessionKey {
        vni: key.vni,
        src_ip: key.dst_ip.clone(),
        dst_ip: key.src_ip.clone(),
        src_port: key.dst_port,
        dst_port: key.src_port,
        protocol: key.protocol,
    }
}

#[tonic::async_trait]
impl SessionOffloadService for OffloadServiceImpl {
    async fn program_session(
        &self,
        request: Request<ProgramSessionRequest>,
    ) -> Result<Response<ProgramSessionResponse>, Status> {
        let req = request.into_inner();
        let key = req.key.ok_or_else(|| Status::invalid_argument("session key is required"))?;

        tracing::debug!(
            vni = key.vni,
            src = %key.src_ip,
            dst = %key.dst_ip,
            action = req.action,
            bidirectional = req.bidirectional,
            "programming session"
        );

        // Program forward entry
        let forward_handle = match self.doca_manager.program_session(
            key.vni,
            &key.src_ip,
            &key.dst_ip,
            key.src_port,
            key.dst_port,
            key.protocol,
            req.action,
            req.forward_port,
        ) {
            Ok(h) => h,
            Err(e) => {
                tracing::error!(error = %e, "failed to program forward session");
                return Ok(Response::new(ProgramSessionResponse {
                    success: false,
                    entry_handle: String::new(),
                    error_message: e.to_string(),
                }));
            }
        };

        // Program reverse entry if bidirectional
        let reverse_handle = if req.bidirectional {
            match self.doca_manager.program_session(
                key.vni,
                &key.dst_ip,
                &key.src_ip,
                key.dst_port,
                key.src_port,
                key.protocol,
                req.action,
                req.forward_port,
            ) {
                Ok(h) => Some(h),
                Err(e) => {
                    // Roll back forward entry
                    let _ = self.doca_manager.remove_session(forward_handle);
                    tracing::error!(error = %e, "failed to program reverse session, rolled back forward");
                    return Ok(Response::new(ProgramSessionResponse {
                        success: false,
                        entry_handle: String::new(),
                        error_message: format!("reverse entry failed: {}", e),
                    }));
                }
            }
        } else {
            None
        };

        // Insert into session table
        let entry = SessionEntry::new(
            forward_handle,
            key.vni,
            key.clone(),
            req.action,
            req.bidirectional,
            reverse_handle,
            req.rule_id.clone(),
        );
        self.session_table.insert(&key, entry);

        // If bidirectional, also insert reverse key entry
        if let Some(rev_handle) = reverse_handle {
            let rev_key = reverse_key(&key);
            let rev_entry = SessionEntry::new(
                rev_handle,
                key.vni,
                rev_key.clone(),
                req.action,
                true,
                Some(forward_handle),
                req.rule_id,
            );
            self.session_table.insert(&rev_key, rev_entry);
        }

        tracing::info!(
            handle = forward_handle,
            reverse_handle = ?reverse_handle,
            "session programmed successfully"
        );

        Ok(Response::new(ProgramSessionResponse {
            success: true,
            entry_handle: forward_handle.to_string(),
            error_message: String::new(),
        }))
    }

    async fn flush_sessions(
        &self,
        request: Request<FlushSessionsRequest>,
    ) -> Result<Response<FlushSessionsResponse>, Status> {
        let req = request.into_inner();

        let criteria = req
            .match_criteria
            .ok_or_else(|| Status::invalid_argument("match_criteria is required"))?;

        let (count, handles) = match criteria {
            MatchCriteria::RuleId(rule_id) => {
                tracing::info!(rule_id = %rule_id, "flushing sessions by rule_id");
                self.session_table.flush_by_rule_id(&rule_id)
            }
            MatchCriteria::Vni(vni) => {
                tracing::info!(vni, "flushing sessions by VNI");
                self.session_table.flush_by_vni(vni)
            }
            MatchCriteria::All(true) => {
                tracing::info!("flushing all sessions");
                self.session_table.flush_all()
            }
            MatchCriteria::All(false) => {
                return Ok(Response::new(FlushSessionsResponse { flushed_count: 0 }));
            }
        };

        // Remove from hardware
        for (handle, reverse_handle) in &handles {
            if let Err(e) = self.doca_manager.remove_session(*handle) {
                tracing::warn!(handle, error = %e, "failed to remove session from hardware during flush");
            }
            if let Some(rev) = reverse_handle {
                if let Err(e) = self.doca_manager.remove_session(*rev) {
                    tracing::warn!(handle = rev, error = %e, "failed to remove reverse session from hardware during flush");
                }
            }
        }

        tracing::info!(flushed = count, "flush complete");

        Ok(Response::new(FlushSessionsResponse {
            flushed_count: count,
        }))
    }

    async fn query_session(
        &self,
        request: Request<QuerySessionRequest>,
    ) -> Result<Response<QuerySessionResponse>, Status> {
        let req = request.into_inner();
        let key = req.key.ok_or_else(|| Status::invalid_argument("session key is required"))?;

        let entry_ref = match self.session_table.get(&key) {
            Some(e) => e,
            None => {
                return Ok(Response::new(QuerySessionResponse {
                    found: false,
                    packets_matched: 0,
                    bytes_matched: 0,
                    age_seconds: 0,
                    offloaded: false,
                }));
            }
        };

        let handle = entry_ref.entry_handle;
        let created_at = entry_ref.created_at;
        drop(entry_ref);

        // Query hardware counters
        let (packets, bytes) = match self.doca_manager.query_session(handle) {
            Ok((p, b)) => (p, b),
            Err(e) => {
                tracing::warn!(handle, error = %e, "failed to query hardware counters");
                (0, 0)
            }
        };

        // Update cached counters in session table
        if let Some(entry_ref) = self.session_table.get(&key) {
            entry_ref.update_counters(packets, bytes);
        }

        let age_seconds = created_at.elapsed().as_secs();

        Ok(Response::new(QuerySessionResponse {
            found: true,
            packets_matched: packets,
            bytes_matched: bytes,
            age_seconds,
            offloaded: true,
        }))
    }

    async fn health_check(
        &self,
        _request: Request<HealthCheckRequest>,
    ) -> Result<Response<HealthCheckResponse>, Status> {
        Ok(Response::new(HealthCheckResponse {
            healthy: true,
            active_sessions: self.session_table.active_count(),
            uptime_seconds: self.start_time.elapsed().as_secs(),
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use prism_proto::session_offload_service_server::SessionOffloadService;
    use prism_proto::{Protocol, SessionKey};
    use tonic::Request;

    fn make_service() -> OffloadServiceImpl {
        let session_table = Arc::new(SessionTable::new());
        let doca_manager = Arc::new(DocaFlowManager::init().unwrap());
        OffloadServiceImpl::new(session_table, doca_manager)
    }

    fn make_key(vni: u32, src_port: u32) -> SessionKey {
        SessionKey {
            vni,
            src_ip: "10.0.0.1".to_string(),
            dst_ip: "10.0.0.2".to_string(),
            src_port,
            dst_port: 80,
            protocol: Protocol::Tcp as i32,
        }
    }

    #[tokio::test]
    async fn test_program_session_unidirectional() {
        let svc = make_service();

        let req = Request::new(ProgramSessionRequest {
            key: Some(make_key(100, 5000)),
            action: 1, // FORWARD
            bidirectional: false,
            forward_port: 0,
            rule_id: "rule-1".to_string(),
        });

        let resp = svc.program_session(req).await.unwrap().into_inner();
        assert!(resp.success);
        assert!(!resp.entry_handle.is_empty());
        assert!(resp.error_message.is_empty());
    }

    #[tokio::test]
    async fn test_program_session_bidirectional() {
        let svc = make_service();

        let req = Request::new(ProgramSessionRequest {
            key: Some(make_key(100, 5000)),
            action: 1,
            bidirectional: true,
            forward_port: 0,
            rule_id: "rule-1".to_string(),
        });

        let resp = svc.program_session(req).await.unwrap().into_inner();
        assert!(resp.success);

        // Should have 2 entries (forward + reverse)
        let health_req = Request::new(HealthCheckRequest {});
        let health = svc.health_check(health_req).await.unwrap().into_inner();
        assert_eq!(health.active_sessions, 2);
    }

    #[tokio::test]
    async fn test_program_session_missing_key() {
        let svc = make_service();

        let req = Request::new(ProgramSessionRequest {
            key: None,
            action: 1,
            bidirectional: false,
            forward_port: 0,
            rule_id: "rule-1".to_string(),
        });

        let result = svc.program_session(req).await;
        assert!(result.is_err());
        assert_eq!(result.unwrap_err().code(), tonic::Code::InvalidArgument);
    }

    #[tokio::test]
    async fn test_query_session_found() {
        let svc = make_service();

        let key = make_key(100, 5000);
        let prog_req = Request::new(ProgramSessionRequest {
            key: Some(key.clone()),
            action: 1,
            bidirectional: false,
            forward_port: 0,
            rule_id: "rule-1".to_string(),
        });
        svc.program_session(prog_req).await.unwrap();

        let query_req = Request::new(QuerySessionRequest {
            key: Some(key),
        });
        let resp = svc.query_session(query_req).await.unwrap().into_inner();
        assert!(resp.found);
        assert!(resp.offloaded);
        assert!(resp.packets_matched > 0);
    }

    #[tokio::test]
    async fn test_query_session_not_found() {
        let svc = make_service();

        let query_req = Request::new(QuerySessionRequest {
            key: Some(make_key(999, 9999)),
        });
        let resp = svc.query_session(query_req).await.unwrap().into_inner();
        assert!(!resp.found);
        assert_eq!(resp.packets_matched, 0);
    }

    #[tokio::test]
    async fn test_flush_by_rule_id() {
        let svc = make_service();

        // Program 3 sessions with rule-A, 2 with rule-B
        for i in 0..3 {
            let req = Request::new(ProgramSessionRequest {
                key: Some(make_key(100, 5000 + i)),
                action: 1,
                bidirectional: false,
                forward_port: 0,
                rule_id: "rule-A".to_string(),
            });
            svc.program_session(req).await.unwrap();
        }
        for i in 0..2 {
            let req = Request::new(ProgramSessionRequest {
                key: Some(make_key(200, 6000 + i)),
                action: 1,
                bidirectional: false,
                forward_port: 0,
                rule_id: "rule-B".to_string(),
            });
            svc.program_session(req).await.unwrap();
        }

        let flush_req = Request::new(FlushSessionsRequest {
            match_criteria: Some(MatchCriteria::RuleId("rule-A".to_string())),
        });
        let resp = svc.flush_sessions(flush_req).await.unwrap().into_inner();
        assert_eq!(resp.flushed_count, 3);

        // Check remaining
        let health_req = Request::new(HealthCheckRequest {});
        let health = svc.health_check(health_req).await.unwrap().into_inner();
        assert_eq!(health.active_sessions, 2);
    }

    #[tokio::test]
    async fn test_flush_by_vni() {
        let svc = make_service();

        for i in 0..4 {
            let req = Request::new(ProgramSessionRequest {
                key: Some(make_key(100, 5000 + i)),
                action: 1,
                bidirectional: false,
                forward_port: 0,
                rule_id: "rule-A".to_string(),
            });
            svc.program_session(req).await.unwrap();
        }

        let flush_req = Request::new(FlushSessionsRequest {
            match_criteria: Some(MatchCriteria::Vni(100)),
        });
        let resp = svc.flush_sessions(flush_req).await.unwrap().into_inner();
        assert_eq!(resp.flushed_count, 4);
    }

    #[tokio::test]
    async fn test_flush_all() {
        let svc = make_service();

        for i in 0..5 {
            let req = Request::new(ProgramSessionRequest {
                key: Some(make_key(100, 5000 + i)),
                action: 1,
                bidirectional: false,
                forward_port: 0,
                rule_id: "rule-A".to_string(),
            });
            svc.program_session(req).await.unwrap();
        }

        let flush_req = Request::new(FlushSessionsRequest {
            match_criteria: Some(MatchCriteria::All(true)),
        });
        let resp = svc.flush_sessions(flush_req).await.unwrap().into_inner();
        assert_eq!(resp.flushed_count, 5);

        let health_req = Request::new(HealthCheckRequest {});
        let health = svc.health_check(health_req).await.unwrap().into_inner();
        assert_eq!(health.active_sessions, 0);
    }

    #[tokio::test]
    async fn test_health_check() {
        let svc = make_service();

        let req = Request::new(HealthCheckRequest {});
        let resp = svc.health_check(req).await.unwrap().into_inner();
        assert!(resp.healthy);
        assert_eq!(resp.active_sessions, 0);
        // uptime should be very small in a test
        assert!(resp.uptime_seconds < 5);
    }
}
