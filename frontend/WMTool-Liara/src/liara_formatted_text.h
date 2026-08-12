#ifndef LIARA_FORMATTED_TEXT_H
#define LIARA_FORMATTED_TEXT_H

#include <gtk/gtk.h>

/* Formatted text rendering for code blocks, markdown, and styled content */

typedef struct {
    char *language;         /* e.g. "python", "c", "json" */
    char *code;             /* Raw code content */
    gboolean has_copy;      /* Show copy button */
} FormattedCodeBlock;

typedef struct {
    gchar *text;
    gboolean is_code;
    gboolean is_bold;
    gboolean is_italic;
    const char *language;
    GtkWidget *widget;
} FormattedTextSegment;

/* Create a formatted code block widget with copy button and syntax label */
GtkWidget *liara_create_code_block(
    const char *code,
    const char *language,
    gboolean show_copy_button
);

/* Parse markdown-like text and return a widget with formatted segments */
GtkWidget *liara_create_formatted_text(const char *text);

/* Extract code language from fence markers (```lang) */
char *liara_extract_code_language(const char *text, size_t *code_start);

/* Simple markdown code fence parser */
typedef struct {
    GSList *segments;  /* List of FormattedTextSegment */
} ParsedFormattedText;

ParsedFormattedText *liara_parse_formatted_text(const char *text);
void liara_parsed_formatted_text_free(ParsedFormattedText *parsed);

#endif
