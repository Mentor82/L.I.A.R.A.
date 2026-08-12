project('liara-server-manager', 'c', version: '0.1.0', default_options: ['warning_level=2', 'c_std=c11'])

server_manager_version = meson.project_version()
gtk_dep = dependency('gtk4')
libsoup_dep = dependency('libsoup-3.0')
json_glib_dep = dependency('json-glib-1.0')

server_manager_lib_sources = files(
  'src/server_manager_app.c',
  'src/manager_ui.c',
  'src/manager_runtime.c',
  'src/manager_config.c',
  'src/manager_logging.c',
  'src/manager_process.c',
  'src/manager_health.c',
)

server_manager_sources = files(
  'src/server_manager.c',
)

server_manager_inc = include_directories('include')

server_manager_lib = static_library(
  'liara-server-manager-app',
  server_manager_lib_sources,
  include_directories: server_manager_inc,
  dependencies: [gtk_dep, libsoup_dep, json_glib_dep],
  c_args: ['-DLIARA_SERVER_MANAGER_VERSION="@0@"'.format(server_manager_version)],
)

executable(
  'liara-server-manager',
  server_manager_sources,
  include_directories: server_manager_inc,
  dependencies: [gtk_dep, libsoup_dep, json_glib_dep],
  link_with: server_manager_lib,
  link_args: host_machine.system() == 'windows' ? ['-luser32'] : [],
  install: false,
)
