project('liara-server-manager', 'c', version: '0.1.0', default_options: ['warning_level=2', 'c_std=c11'])

server_manager_version = meson.project_version()

gtk_dep = dependency('gtk4')
libsoup_dep = dependency('libsoup-3.0')
json_glib_dep = dependency('json-glib-1.0')

server_manager_sources = files(
  'src/server_manager.c',
)

executable(
  'liara-server-manager',
  server_manager_sources,
  dependencies: [gtk_dep, libsoup_dep, json_glib_dep],
  c_args: ['-DLIARA_SERVER_MANAGER_VERSION="@0@"'.format(server_manager_version)],
  link_args: host_machine.system() == 'windows' ? ['-luser32'] : [],
  install: false,
)
