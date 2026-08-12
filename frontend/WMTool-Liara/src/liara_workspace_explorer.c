#include "liara_workspace_explorer.h"
#include <string.h>
#include <gio/gio.h>
#include <glib/gstdio.h>

/* File list item for tile view */
typedef struct {
    char *name;
    char *path;
    gboolean is_directory;
    GtkWidget *tile_widget;
} TileFileItem;

/* Populate folder tree from directory */
static void
populate_folder_tree(
    LiaraWorkspaceExplorer *explorer,
    GtkTreeIter *parent,
    const char *path)
{
    GDir *dir;
    const char *name;
    GError *error = NULL;
    
    if (path == NULL) {
        return;
    }
    
    dir = g_dir_open(path, 0, &error);
    if (dir == NULL) {
        if (error != NULL) {
            g_error_free(error);
        }
        return;
    }
    
    while ((name = g_dir_read_name(dir)) != NULL) {
        g_autofree char *full_path = g_build_filename(path, name, NULL);
        
        if (g_file_test(full_path, G_FILE_TEST_IS_DIR) && 
            name[0] != '.') {  /* Skip hidden folders */
            GtkTreeIter child;
            gtk_tree_store_append(explorer->folder_store, &child, parent);
            gtk_tree_store_set(explorer->folder_store, &child,
                             0, name,
                             1, full_path,
                             -1);
        }
    }
    
    g_dir_close(dir);
}

/* Load files into detail view */
static void
load_detail_view(
    LiaraWorkspaceExplorer *explorer,
    const char *path)
{
    GDir *dir;
    const char *name;
    GError *error = NULL;
    
    gtk_tree_store_clear(explorer->detail_store);
    
    if (path == NULL) {
        return;
    }
    
    dir = g_dir_open(path, 0, &error);
    if (dir == NULL) {
        if (error != NULL) {
            g_error_free(error);
        }
        return;
    }
    
    while ((name = g_dir_read_name(dir)) != NULL) {
        g_autofree char *full_path = g_build_filename(path, name, NULL);
        GtkTreeIter iter;
        g_autofree char *type_str = NULL;
        goffset size = 0;
        g_autofree char *size_str = NULL;
        
        if (g_file_test(full_path, G_FILE_TEST_IS_DIR)) {
            type_str = g_strdup("[Folder]");
        } else {
            type_str = g_strdup("[File]");
            
            GStatBuf stat_info;
            if (g_stat(full_path, &stat_info) == 0) {
                size = stat_info.st_size;
                if (size > 1024 * 1024) {
                    size_str = g_strdup_printf("%.1f MB", size / (1024.0 * 1024.0));
                } else if (size > 1024) {
                    size_str = g_strdup_printf("%.1f KB", size / 1024.0);
                } else {
                    size_str = g_strdup_printf("%lld B", (long long)size);
                }
            }
        }
        
        if (size_str == NULL) {
            size_str = g_strdup("-");
        }
        
        gtk_tree_store_append(explorer->detail_store, &iter, NULL);
        gtk_tree_store_set(explorer->detail_store, &iter,
                         0, name,
                         1, type_str,
                         2, size_str,
                         3, full_path,
                         -1);
    }
    
    g_dir_close(dir);
}

/* Load files into tile view */
static void
load_tile_view(
    LiaraWorkspaceExplorer *explorer,
    const char *path)
{
    GDir *dir;
    const char *name;
    GError *error = NULL;
    
    /* Clear existing children */
    GtkWidget *child;
    while ((child = gtk_widget_get_first_child(GTK_WIDGET(explorer->tile_view))) != NULL) {
        gtk_flow_box_remove(explorer->tile_view, child);
    }
    
    if (path == NULL) {
        return;
    }
    
    dir = g_dir_open(path, 0, &error);
    if (dir == NULL) {
        if (error != NULL) {
            g_error_free(error);
        }
        return;
    }
    
    while ((name = g_dir_read_name(dir)) != NULL) {
        g_autofree char *full_path = g_build_filename(path, name, NULL);
        GtkWidget *tile = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
        GtkWidget *icon_label;
        GtkWidget *name_label;
        
        gtk_widget_add_css_class(tile, "explorer-tile");
        
        /* Icon */
        if (g_file_test(full_path, G_FILE_TEST_IS_DIR)) {
            icon_label = gtk_label_new("📁");
        } else {
            icon_label = gtk_label_new("📄");
        }
        gtk_widget_add_css_class(icon_label, "explorer-tile-icon");
        gtk_label_set_markup(GTK_LABEL(icon_label), 
                           g_file_test(full_path, G_FILE_TEST_IS_DIR) ? 
                           "<span font='18'>📁</span>" : 
                           "<span font='18'>📄</span>");
        
        /* Name */
        name_label = gtk_label_new(name);
        gtk_label_set_wrap(GTK_LABEL(name_label), TRUE);
        gtk_label_set_max_width_chars(GTK_LABEL(name_label), 12);
        gtk_widget_add_css_class(name_label, "explorer-tile-name");
        
        gtk_box_append(GTK_BOX(tile), icon_label);
        gtk_box_append(GTK_BOX(tile), name_label);
        gtk_flow_box_append(explorer->tile_view, tile);
        
        g_object_set_data_full(G_OBJECT(tile), "path", 
                             g_strdup(full_path), g_free);
    }
    
    g_dir_close(dir);
}

/* Create workspace explorer */
LiaraWorkspaceExplorer *
liara_workspace_explorer_new(const char *root_path)
{
    LiaraWorkspaceExplorer *explorer = g_new0(LiaraWorkspaceExplorer, 1);
    GtkWidget *container;
    GtkWidget *left_panel;
    GtkWidget *toolbar;
    GtkWidget *view_buttons;
    
    /* Main container */
    container = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    explorer->container = GTK_BOX(container);
    
    /* Toolbar */
    toolbar = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    gtk_widget_add_css_class(toolbar, "explorer-toolbar");
    gtk_widget_set_margin_start(toolbar, 8);
    gtk_widget_set_margin_end(toolbar, 8);
    gtk_widget_set_margin_top(toolbar, 8);
    gtk_widget_set_margin_bottom(toolbar, 8);
    
    /* Path entry */
    explorer->path_entry = GTK_ENTRY(gtk_entry_new());
    gtk_widget_set_hexpand(GTK_WIDGET(explorer->path_entry), TRUE);
    gtk_editable_set_text(GTK_EDITABLE(explorer->path_entry), root_path != NULL ? root_path : "/home/liara");
    gtk_widget_add_css_class(GTK_WIDGET(explorer->path_entry), "explorer-path");
    gtk_box_append(GTK_BOX(toolbar), GTK_WIDGET(explorer->path_entry));
    
    /* View mode selector */
    explorer->view_mode_combo = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
    gtk_combo_box_text_append(explorer->view_mode_combo, "details", "📋 Details");
    gtk_combo_box_text_append(explorer->view_mode_combo, "tiles", "🎨 Tiles");
    gtk_combo_box_set_active(GTK_COMBO_BOX(explorer->view_mode_combo), 0);
    gtk_widget_add_css_class(GTK_WIDGET(explorer->view_mode_combo), "explorer-view-mode");
    gtk_box_append(GTK_BOX(toolbar), GTK_WIDGET(explorer->view_mode_combo));
    
    gtk_box_append(GTK_BOX(container), toolbar);
    
    /* Main pane: left (folder tree) and right (files) */
    explorer->main_pane = GTK_PANED(gtk_paned_new(GTK_ORIENTATION_HORIZONTAL));
    gtk_paned_set_start_child(explorer->main_pane, NULL);
    gtk_paned_set_position(explorer->main_pane, 200);
    gtk_paned_set_resize_start_child(explorer->main_pane, FALSE);
    gtk_widget_set_hexpand(GTK_WIDGET(explorer->main_pane), TRUE);
    gtk_widget_set_vexpand(GTK_WIDGET(explorer->main_pane), TRUE);
    
    /* Left panel: folder tree */
    left_panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    gtk_widget_add_css_class(left_panel, "explorer-left-panel");
    
    explorer->folder_store = gtk_tree_store_new(2, G_TYPE_STRING, G_TYPE_STRING);
    explorer->folder_tree = GTK_TREE_VIEW(gtk_tree_view_new_with_model(
        GTK_TREE_MODEL(explorer->folder_store)));
    gtk_widget_add_css_class(GTK_WIDGET(explorer->folder_tree), "explorer-tree");
    
    GtkCellRenderer *renderer = gtk_cell_renderer_text_new();
    GtkTreeViewColumn *column = gtk_tree_view_column_new_with_attributes(
        "Folders", renderer, "text", 0, NULL);
    gtk_tree_view_append_column(explorer->folder_tree, column);
    
    explorer->folder_scroller = gtk_scrolled_window_new();
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(explorer->folder_scroller),
                                GTK_WIDGET(explorer->folder_tree));
    gtk_box_append(GTK_BOX(left_panel), explorer->folder_scroller);
    
    /* Right panel: file list and preview */
    explorer->right_panel = GTK_BOX(gtk_box_new(GTK_ORIENTATION_VERTICAL, 0));
    
    /* Detail view */
    explorer->detail_store = gtk_tree_store_new(4,
                                               G_TYPE_STRING,  /* Name */
                                               G_TYPE_STRING,  /* Type */
                                               G_TYPE_STRING,  /* Size */
                                               G_TYPE_STRING); /* Path */
    explorer->detail_view = GTK_TREE_VIEW(gtk_tree_view_new_with_model(
        GTK_TREE_MODEL(explorer->detail_store)));
    gtk_widget_add_css_class(GTK_WIDGET(explorer->detail_view), "explorer-detail");
    
    /* Columns for detail view */
    GtkCellRenderer *name_renderer = gtk_cell_renderer_text_new();
    GtkTreeViewColumn *name_column = gtk_tree_view_column_new_with_attributes(
        "Name", name_renderer, "text", 0, NULL);
    gtk_tree_view_append_column(explorer->detail_view, name_column);
    
    GtkCellRenderer *type_renderer = gtk_cell_renderer_text_new();
    GtkTreeViewColumn *type_column = gtk_tree_view_column_new_with_attributes(
        "Type", type_renderer, "text", 1, NULL);
    gtk_tree_view_append_column(explorer->detail_view, type_column);
    
    GtkCellRenderer *size_renderer = gtk_cell_renderer_text_new();
    GtkTreeViewColumn *size_column = gtk_tree_view_column_new_with_attributes(
        "Size", size_renderer, "text", 2, NULL);
    gtk_tree_view_append_column(explorer->detail_view, size_column);
    
    explorer->detail_scroller = gtk_scrolled_window_new();
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(explorer->detail_scroller),
                                GTK_WIDGET(explorer->detail_view));
    gtk_widget_set_hexpand(explorer->detail_scroller, TRUE);
    gtk_widget_set_vexpand(explorer->detail_scroller, TRUE);
    gtk_box_append(explorer->right_panel, explorer->detail_scroller);
    
    /* Tile view */
    explorer->tile_view = GTK_FLOW_BOX(gtk_flow_box_new());
    gtk_widget_add_css_class(GTK_WIDGET(explorer->tile_view), "explorer-tiles");
    gtk_flow_box_set_selection_mode(explorer->tile_view, GTK_SELECTION_SINGLE);
    gtk_flow_box_set_homogeneous(explorer->tile_view, FALSE);
    gtk_flow_box_set_max_children_per_line(explorer->tile_view, 4);
    gtk_widget_set_hexpand(GTK_WIDGET(explorer->tile_view), TRUE);
    gtk_widget_set_vexpand(GTK_WIDGET(explorer->tile_view), TRUE);
    
    GtkWidget *tile_scroller = gtk_scrolled_window_new();
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(tile_scroller),
                                GTK_WIDGET(explorer->tile_view));
    gtk_box_append(explorer->right_panel, tile_scroller);
    gtk_widget_hide(tile_scroller);  /* Hidden by default */
    
    gtk_paned_set_start_child(explorer->main_pane, left_panel);
    gtk_paned_set_end_child(explorer->main_pane, GTK_WIDGET(explorer->right_panel));
    
    gtk_box_append(GTK_BOX(container), GTK_WIDGET(explorer->main_pane));
    gtk_widget_set_hexpand(GTK_WIDGET(explorer->main_pane), TRUE);
    gtk_widget_set_vexpand(GTK_WIDGET(explorer->main_pane), TRUE);
    
    explorer->current_path = g_strdup(root_path != NULL ? root_path : "/home/liara");
    explorer->current_view_mode = EXPLORER_VIEW_DETAILS;
    
    return explorer;
}

void
liara_workspace_explorer_navigate(
    LiaraWorkspaceExplorer *explorer,
    const char *path)
{
    if (explorer == NULL || path == NULL) {
        return;
    }
    
    g_free(explorer->current_path);
    explorer->current_path = g_strdup(path);
    gtk_editable_set_text(GTK_EDITABLE(explorer->path_entry), path);
    
    if (explorer->current_view_mode == EXPLORER_VIEW_DETAILS) {
        load_detail_view(explorer, path);
    } else {
        load_tile_view(explorer, path);
    }
}

void
liara_workspace_explorer_set_view_mode(
    LiaraWorkspaceExplorer *explorer,
    ExplorerViewMode mode)
{
    if (explorer == NULL) {
        return;
    }
    
    explorer->current_view_mode = mode;
    
    if (mode == EXPLORER_VIEW_DETAILS) {
        gtk_widget_show(explorer->detail_scroller);
        gtk_widget_hide(gtk_widget_get_parent(GTK_WIDGET(explorer->tile_view)));
        load_detail_view(explorer, explorer->current_path);
    } else {
        gtk_widget_hide(explorer->detail_scroller);
        gtk_widget_show(gtk_widget_get_parent(GTK_WIDGET(explorer->tile_view)));
        load_tile_view(explorer, explorer->current_path);
    }
}

char *
liara_workspace_explorer_get_selected(
    LiaraWorkspaceExplorer *explorer)
{
    if (explorer == NULL) {
        return g_strdup("");
    }
    
    if (explorer->current_view_mode == EXPLORER_VIEW_DETAILS) {
        GtkTreeSelection *selection = gtk_tree_view_get_selection(explorer->detail_view);
        GtkTreeModel *model;
        GtkTreeIter iter;
        
        if (gtk_tree_selection_get_selected(selection, &model, &iter)) {
            g_autofree char *path = NULL;
            gtk_tree_model_get(model, &iter, 3, &path, -1);
            return g_strdup(path != NULL ? path : "");
        }
    }
    
    return g_strdup("");
}

GtkWidget *
liara_workspace_explorer_get_widget(
    LiaraWorkspaceExplorer *explorer)
{
    return explorer != NULL ? GTK_WIDGET(explorer->container) : NULL;
}

void
liara_workspace_explorer_free(LiaraWorkspaceExplorer *explorer)
{
    if (explorer != NULL) {
        g_free(explorer->current_path);
        g_free(explorer);
    }
}
