#
# DOCA Flow CT Firewall Daemon
# Uses the DOCA 3.4 sample framework for device/port initialization.
#

project('doca_firewall', 'C', 'CPP',
    version: '0.1.0',
    license: 'BSD-3',
    default_options: ['buildtype=debug'],
    meson_version: '>= 0.61.2'
)

SAMPLE_NAME = 'doca_firewall'

# Allow experimental DOCA APIs
add_project_arguments('-D DOCA_ALLOW_EXPERIMENTAL_API', language: ['c', 'cpp'])
add_project_arguments('-D_GNU_SOURCE', language: ['c', 'cpp'])

sample_dependencies = []
# Required for all DOCA programs
sample_dependencies += dependency('doca-common')
# DOCA Flow library
sample_dependencies += dependency('doca-flow')
# DOCA DPDK bridge
sample_dependencies += dependency('doca-dpdk-bridge')
# DOCA argument parser
sample_dependencies += dependency('doca-argp')
# DPDK
sample_dependencies += dependency('libdpdk')
# pthreads
sample_dependencies += dependency('threads')

# Check flavour of DPDK used
c_compiler = meson.get_compiler('c')
if c_compiler.has_header_symbol('rte_pmd_mlx5.h',
                'rte_pmd_mlx5_driver_disable_steering',
                dependencies: dependency('libdpdk'),
                prefix: '#include <rte_pmd_mlx5.h>')
    add_project_arguments('-D DOCA_BUNDLE_DPDK_FOUND', language: ['c', 'cpp'])
    message('Found rte_pmd_mlx5_driver_disable_steering(), using DOCA-Bundle DPDK')
elif c_compiler.has_header_symbol('rte_pmd_mlx5.h',
                'rte_pmd_mlx5_disable_steering',
                dependencies: dependency('libdpdk'),
                prefix: '#include <rte_pmd_mlx5.h>')
    add_project_arguments('-D UPSTREAM_DPDK_FOUND', language: ['c', 'cpp'])
    message('Found rte_pmd_mlx5_disable_steering(), using Upstream DPDK')
else
    warning('Unable to find rte_pmd_mlx5_driver_disable_steering() or rte_pmd_mlx5_disable_steering()')
endif

# DOCA sample common source paths (installed with DOCA SDK)
doca_samples_dir = '/opt/mellanox/doca/samples'
doca_apps_dir = '/opt/mellanox/doca/applications'

sample_srcs = [
    # Our firewall sources
    'src/main.c',
    'src/flow_init.c',
    'src/policy.c',
    'src/ct_offload.c',
    'src/rest_api.c',
    'src/metrics.c',
    # DOCA Flow common code (provides init_doca_flow, port init, etc.)
    doca_samples_dir / 'doca_flow' / 'flow_common.c',
    # DOCA Flow switch common code (provides init_doca_flow_switch_ports)
    doca_samples_dir / 'doca_flow' / 'flow_switch_common.c',
    # DOCA Flow CT common code (provides CT init helpers)
    doca_samples_dir / 'doca_flow' / 'flow_ct_common.c',
    # DOCA common code (provides open_doca_device_with_pci, etc.)
    doca_samples_dir / 'common.c',
    # DPDK utils (provides dpdk_queues_and_ports_init, etc.)
    doca_apps_dir / 'common' / 'dpdk_utils.c',
]

sample_inc_dirs = []
# Our source directory
sample_inc_dirs += include_directories('src')
# DOCA Flow sample common headers
sample_inc_dirs += include_directories(doca_samples_dir / 'doca_flow')
# DOCA sample common headers
sample_inc_dirs += include_directories(doca_samples_dir)
# DOCA application common headers
sample_inc_dirs += include_directories(doca_apps_dir / 'common')

executable(SAMPLE_NAME, sample_srcs,
    c_args : '-Wno-missing-braces',
    dependencies : sample_dependencies,
    include_directories: sample_inc_dirs,
    install: false)
