project('liara-gtk-ui', 'c', version: '0.1.0', default_options: ['warning_level=2', 'c_std=c11'])

gtk_dep = dependency('gtk4')
libsoup_dep = dependency('libsoup-3.0')
json_glib_dep = dependency('json-glib-1.0')

sources = files(
  'src/main.c',
  'src/liara_api.c',
  'src/liara_window.c',
)

executable(
  'liara-gtk-ui',
  sources,
  dependencies: [gtk_dep, libsoup_dep, json_glib_dep],
  install: false,
)

configure_file(
  input: 'style.css',
  output: 'style.css',
  copy: true,
)
