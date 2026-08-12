project('liara-gtk-ui', 'c', version: '0.1.0', default_options: ['warning_level=2', 'c_std=c11'])

cc = meson.get_compiler('c')
gtk_dep = dependency('gtk4')
libsoup_dep = dependency('libsoup-3.0')
json_glib_dep = dependency('json-glib-1.0')
extra_deps = []

if host_machine.system() == 'windows'
  secur32_dep = cc.find_library('secur32', required: false)
  if secur32_dep.found()
    extra_deps += secur32_dep
  endif
endif

api_sources = files('src/liara_api.c')
window_sources = files('src/liara_window.c')
formatted_text_sources = files('src/liara_formatted_text.c')
chat_input_sources = files('src/liara_chat_input.c')
workspace_explorer_sources = files('src/liara_workspace_explorer.c')
main_sources = files('src/main.c')

liara_api_lib = shared_library(
  'liara-api',
  api_sources,
  dependencies: [gtk_dep, libsoup_dep, json_glib_dep] + extra_deps,
  install: false,
)

liara_formatted_text_lib = shared_library(
  'liara-formatted-text',
  formatted_text_sources,
  dependencies: [gtk_dep] + extra_deps,
  install: false,
)

liara_chat_input_lib = shared_library(
  'liara-chat-input',
  chat_input_sources,
  dependencies: [gtk_dep] + extra_deps,
  install: false,
)

liara_workspace_explorer_lib = shared_library(
  'liara-workspace-explorer',
  workspace_explorer_sources,
  dependencies: [gtk_dep] + extra_deps,
  install: false,
)

liara_window_lib = shared_library(
  'liara-window',
  window_sources,
  dependencies: [gtk_dep, libsoup_dep, json_glib_dep] + extra_deps,
  link_with: [liara_api_lib, liara_formatted_text_lib, liara_chat_input_lib, liara_workspace_explorer_lib],
  install: false,
)

executable(
  'liara-gtk-ui',
  main_sources,
  dependencies: [gtk_dep, libsoup_dep, json_glib_dep] + extra_deps,
  link_with: [liara_api_lib, liara_window_lib],
  install: false,
)

configure_file(
  input: 'style.css',
  output: 'style.css',
  copy: true,
)
