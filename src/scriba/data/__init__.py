"""The data: file formats, catalogues, tables, SQLite. Standard library and
format readers only; where a file lives is the caller's to say. It knows no
other layer, the core included: a format that writes core's segments asks
for their shape (lines.Line), not for core's type. Here: the transcript's
files (srt, markdown, txt), a meeting's own file (session), the times as
the files write them (timing) and the rules for a produced file (files)."""
