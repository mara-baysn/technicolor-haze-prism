use std::net::Ipv4Addr;
use std::time::Duration;

use prism_proto::session_offload_service_client::SessionOffloadServiceClient;
use prism_proto::{
    flush_sessions_request, FlushSessionsRequest, ProgramSessionRequest, QuerySessionRequest,
    SessionKey,
};
use tonic::transport::Channel;

use crate::conntrack::FiveTuple;
use crate::offload::{OffloadAction, OffloadRequest};

/// gRPC client wrapping the DPU offload daemon's SessionOffloadService.
pub struct OffloadClient {
    client: SessionOffloadServiceClient<Channel>,
}

const MAX_RETRIES: u32 = 3;
const BACKOFF_BASE: [u64; 3] = [10, 100, 1000]; // ms

impl OffloadClient {
    /// Connect to the offload daemon at the given address.
    pub async fn connect(addr: &str) -> anyhow::Result<Self> {
        let channel = Channel::from_shared(addr.to_string())?
            .connect_timeout(Duration::from_secs(5))
            .timeout(Duration::from_secs(10))
            .connect()
            .await?;
        let client = SessionOffloadServiceClient::new(channel);
        Ok(Self { client })
    }

    /// Program a session offload entry on the DPU. Returns the entry handle on success.
    pub async fn program_session(&mut self, req: &OffloadRequest) -> anyhow::Result<String> {
        let proto_req = Self::build_program_request(req);

        let mut last_err = None;
        for attempt in 0..MAX_RETRIES {
            match self.client.program_session(proto_req.clone()).await {
                Ok(resp) => {
                    let inner = resp.into_inner();
                    if inner.success {
                        return Ok(inner.entry_handle);
                    }
                    return Err(anyhow::anyhow!(
                        "program_session failed: {}",
                        inner.error_message
                    ));
                }
                Err(status) => {
                    last_err = Some(status);
                    if attempt < MAX_RETRIES - 1 {
                        tokio::time::sleep(Duration::from_millis(BACKOFF_BASE[attempt as usize]))
                            .await;
                    }
                }
            }
        }
        Err(anyhow::anyhow!(
            "program_session failed after {} retries: {}",
            MAX_RETRIES,
            last_err.unwrap()
        ))
    }

    /// Flush sessions matching the given criteria. Returns the number of sessions flushed.
    pub async fn flush_sessions(
        &mut self,
        rule_id: Option<&str>,
        vni: Option<u32>,
    ) -> anyhow::Result<u32> {
        let match_criteria = if let Some(rid) = rule_id {
            Some(flush_sessions_request::MatchCriteria::RuleId(
                rid.to_string(),
            ))
        } else if let Some(v) = vni {
            Some(flush_sessions_request::MatchCriteria::Vni(v))
        } else {
            Some(flush_sessions_request::MatchCriteria::All(true))
        };

        let req = FlushSessionsRequest { match_criteria };

        let mut last_err = None;
        for attempt in 0..MAX_RETRIES {
            match self.client.flush_sessions(req.clone()).await {
                Ok(resp) => {
                    return Ok(resp.into_inner().flushed_count);
                }
                Err(status) => {
                    last_err = Some(status);
                    if attempt < MAX_RETRIES - 1 {
                        tokio::time::sleep(Duration::from_millis(BACKOFF_BASE[attempt as usize]))
                            .await;
                    }
                }
            }
        }
        Err(anyhow::anyhow!(
            "flush_sessions failed after {} retries: {}",
            MAX_RETRIES,
            last_err.unwrap()
        ))
    }

    /// Query a session on the DPU. Returns (packets_matched, bytes_matched).
    pub async fn query_session(
        &mut self,
        vni: u32,
        tuple: &FiveTuple,
    ) -> anyhow::Result<(u64, u64)> {
        let key = Self::five_tuple_to_session_key(vni, tuple);
        let req = QuerySessionRequest { key: Some(key) };

        let mut last_err = None;
        for attempt in 0..MAX_RETRIES {
            match self.client.query_session(req.clone()).await {
                Ok(resp) => {
                    let inner = resp.into_inner();
                    if inner.found {
                        return Ok((inner.packets_matched, inner.bytes_matched));
                    }
                    return Err(anyhow::anyhow!("session not found"));
                }
                Err(status) => {
                    last_err = Some(status);
                    if attempt < MAX_RETRIES - 1 {
                        tokio::time::sleep(Duration::from_millis(BACKOFF_BASE[attempt as usize]))
                            .await;
                    }
                }
            }
        }
        Err(anyhow::anyhow!(
            "query_session failed after {} retries: {}",
            MAX_RETRIES,
            last_err.unwrap()
        ))
    }

    fn build_program_request(req: &OffloadRequest) -> ProgramSessionRequest {
        let key = Self::five_tuple_to_session_key(req.vni, &req.tuple);
        let action = match req.action {
            OffloadAction::Forward => prism_proto::OffloadAction::Forward as i32,
            OffloadAction::Drop => prism_proto::OffloadAction::Drop as i32,
        };
        let rule_id = req
            .rule_id_index
            .map(|idx| format!("rule_{}", idx))
            .unwrap_or_default();

        ProgramSessionRequest {
            key: Some(key),
            action,
            bidirectional: req.bidirectional,
            forward_port: 0,
            rule_id,
        }
    }

    fn five_tuple_to_session_key(vni: u32, tuple: &FiveTuple) -> SessionKey {
        let protocol = match tuple.protocol {
            6 => prism_proto::Protocol::Tcp as i32,
            17 => prism_proto::Protocol::Udp as i32,
            1 => prism_proto::Protocol::Icmp as i32,
            _ => prism_proto::Protocol::Unspecified as i32,
        };

        SessionKey {
            vni,
            src_ip: Ipv4Addr::from(tuple.src_ip).to_string(),
            dst_ip: Ipv4Addr::from(tuple.dst_ip).to_string(),
            src_port: tuple.src_port as u32,
            dst_port: tuple.dst_port as u32,
            protocol,
        }
    }
}
