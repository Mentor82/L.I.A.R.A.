#ifndef LIARA_WORKSPACE_EXPLORER_H
#define LIARA_WORKSPACE_EXPLORER_H

#include <gtk/gtk.h>

/* Windows Explorer-style workspace navigation */

typedef enum {
    EXPLORER_VIEW_DETAILS = 0,   /* List view with details */
    EXPLORER_VIEW_TILES = 1,     /* Grid/tile view */
} ExplorerViewMode;

typedef struct {
    char *path;
    gboolean is_directory;
    gchar *name;
    gchar *modified;
    gsize size;
    GFileType file_type;
} FileItem;

typedef struct {
    GtkBox *container;
    GtkPaned *main_pane;
    
    /* Left side: folder tree */
    GtkTreeView *folder_tree;
    GtkTreeStore *folder_store;
    GtkWidget *folder_scroller;
    
    /* Right side: content */
    GtkBox *right_panel;
    GtkComboBoxText *view_mode_combo;
    GtkEntry *path_entry;
    GtkFlowBox *tile_view;
    GtkTreeView *detail_view;
    GtkTreeStore *detail_store;
    GtkWidget *detail_scroller;
    
    /* Preview */
    GtkWidget *preview_pane;
    GtkTextView *preview_text;
    GtkLabel *preview_info;
    
    char *current_path;
    ExplorerViewMode current_view_mode;
} LiaraWorkspaceExplorer;

/* Create workspace explorer widget */
LiaraWorkspaceExplorer *liara_workspace_explorer_new(const char *root_path);

/* Update the view with files from path */
void liara_workspace_explorer_navigate(
    LiaraWorkspaceExplorer *explorer,
    const char *path
);

/* Set view mode (details or tiles) */
void liara_workspace_explorer_set_view_mode(
    LiaraWorkspaceExplorer *explorer,
    ExplorerViewMode mode
);

/* Get current selected file path */
char *liara_workspace_explorer_get_selected(
    LiaraWorkspaceExplorer *explorer
);

/* Free explorer resources */
void liara_workspace_explorer_free(LiaraWorkspaceExplorer *explorer);

/* Get the main widget */
GtkWidget *liara_workspace_explorer_get_widget(
    LiaraWorkspaceExplorer *explorer
);

#endif
