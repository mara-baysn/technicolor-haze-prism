project('doca-firewall', 'C',
    version: '0.1.0',
    license: 'BSD-3',
    default_options: ['buildtype=debug', 'c_std=gnu11'],
    meson_version: '>= 0.61.2'
)

# Allow experimental DOCA APIs
add_project_arguments('-DDOCA_ALLOW_EXPERIMENTAL_API', language: 'c')
add_project_arguments('-D_GNU_SOURCE', language: 'c')

# Dependencies
deps = []
deps += dependency('doca-common')
deps += dependency('doca-flow')
deps += dependency('doca-dpdk-bridge')
deps += dependency('libdpdk')

# pthreads
thread_dep = dependency('threads')
deps += thread_dep

sources = [
    'src/main.c',
    'src/flow_init.c',
    'src/policy.c',
    'src/ct_offload.c',
    'src/rest_api.c',
    'src/metrics.c',
]

inc = include_directories('src')

executable('doca_firewall', sources,
    dependencies: deps,
    include_directories: inc,
    c_args: ['-Wno-missing-braces'],
    install: false,
)
