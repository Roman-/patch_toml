patch_toml — apply precise value changes to a TOML config while preserving formatting and comments.

# Scope
- Change existing scalar/array/inline-table values at specific TOML paths.
- Optionally set/replace a top-of-file comment block.
- Do not add new sections/keys by default; missing targets are errors.

# Usage
```sh
python3 patch_toml.py INPUT_TOML OUTPUT_TOML [options] --set 'path = TOML_VALUE [# inline comment]' ...
```

Options
Options
--set STRING              Repeatable. One patch per flag.
                         Grammar: path = TOML_VALUE [# inline comment]
                         Examples:
                           'logger.stdout_level = 6 # disable'
                           'device.report_address = ""'
                           'device.out_sources = [2, 2]'
                           'servers[0].host = "localhost"'
--delete-key STRING       Repeatable. Delete a single key by path.
                         Example: 'logger.file_level'
--delete-section STRING   Repeatable. Delete a section (table) by path, non-recursive.
                         Example: 'ups_sim'
                         Arrays-of-tables require an index: 'servers[2]'
--top-comment TEXT        Replace or create a leading top-of-file comment block using TEXT.
                         Supports newlines. Each line is written as '# <line>'.


# Formatting rules
- Any modified assignment is rewritten as:
    key = TOML_VALUE
  or, if a comment is provided:
    key = TOML_VALUE # comment
- Exactly one space around '=' and before '#'.
- Arrays are emitted inline with commas followed by a single space.
- Unmodified lines are left untouched.

# Patch path rules
- Path segments are separated by dots: section.key
- Use TOML quoted keys inside the path if a segment contains spaces or dots:
  "my key".value
- Arrays use zero-based indices in square brackets: items[2].name
- Arrays-of-tables addressed the same way: servers[0].port
- Only one key is targeted per --set or --delete-key

# Inline comment rules
- If an inline comment is provided after '#', it becomes the entire inline comment for that key.
- If no inline comment is provided, any existing inline comment on that key is removed.
- A '#' inside a quoted string is part of the string, not a comment delimiter.

# Top-of-file comment rules
- The top-of-file comment block is the contiguous block of comment or blank lines at the beginning of the file.
- --top-comment replaces that block entirely. If none exists, a new block is inserted at the top.
- A single blank line is inserted after the top-of-file comment block.

# Delete-section rules
- Non-recursive by design. Only the table specified by the exact path is removed.
- Child tables (e.g., some_section.sub) are not removed automatically; delete them with additional --delete-section flags.
- The tool deletes from the table header through its key/value lines until the next table header of any name.
- Standalone comments immediately above or below the removed section are not deleted.

# Behavior
- Modify only targeted keys/sections and the top-of-file comment when requested.
- Preserve unrelated content and comments except where a modified assignment or deleted section requires changes.
- Writing to OUTPUT_TOML is allowed even if it equals INPUT_TOML.

# Validation and fail states (non-zero exit)
1.  input file not found, unreadable, or invalid TOML
2.  requested path (key, section, or array index) not found
3.  ambiguous path (e.g., multiple matches in arrays-of-tables without an index)
4.  invalid option payload (parse error in path or TOML_VALUE)
5.  output path unwritable

# Corner cases
- Empty string values must be quoted: ""
- Keys with dots or spaces must use quoted segments in the path: "a.b".c
- Array index out of range is a not found error
- Deleting a section does not remove adjacent standalone comments
- Duplicate flags targeting the same path are applied in order; the last one wins

# Testing
- Prefer semantic assertions: parse the output with tomllib and assert final values and key/section existence.
- For formatting, assert canonicalization on changed lines only (e.g., "file_level = 6 # disable").
- Verify that unmodified lines remain untouched byte-for-byte.
- Verify that an omitted inline comment removes any existing inline comment.
- Verify non-recursive delete-section behavior and that comments adjacent to the removed section remain.
- Verify top-of-file comment replacement creates exactly one blank line after the block.

# Examples
Change three fields, remove an existing inline comment by omission, delete a key and a non-recursive section, set a top comment:
```sh
  python3 patch_toml.py config.toml config_patched.toml \
    --set 'logger.stdout_level = 6 # disable' \
    --set 'device.report_address = ""' \
    --set 'logger.file_level = 6' \
    --delete-key 'device.shadowing' \
    --delete-section 'simulators' \
    --top-comment 'Auto-patched by CI on build'
```

Formatting expectation example
Input line:
  file_level   = 5   # trace
After:
  python3 patch_toml.py --set 'logger.file_level = 6'
Output line:
  file_level = 6
