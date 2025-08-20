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
--set STRING              Repeatable. One patch per flag. STRING grammar:
                          path = TOML_VALUE [# inline comment]
                          Examples:
                            'logger.stdout_level = 6 # disable'
                            'ups_sim.report_address = ""'
                            'ups_sim.out_sources = [2, 2]'
                            'servers[0].host = "localhost"'
--delete-key STRING       Repeatable. One delete per flag.
--delete-section STRING   Repeatable. One section per flag.
--top-comment TEXT        Add or replace existing leading comment block at the very top with TEXT.

# Patch path rules - TODO stopped here
- Path segments are separated by dots.
- Use TOML quoted keys inside the path if a segment contains spaces or dots:
  --set '"my key".value = 1'
- Arrays use zero-based indices in square brackets: items[2].name.
- Arrays-of-tables are addressed the same way: services[0].port.
- Only one key is targeted per --set.

# Inline comment rules
- If an inline comment is provided after '#', it replaces the existing inline comment for that key.
- If no inline comment is provided, any existing inline comment is preserved.
- The '#' that starts an inline comment is recognized only if it is not inside a quoted TOML string.

# Behavior and guarantees
- Preserve original key order, table order, spacing, and unrelated comments.
- Update/delete only the targeted keys and sections, and their inline comments.

# Validation and fail states (non-zero exit)
1.  input file not found, unreadable or input TOML is invalid
2.  requested path (section/key or array index) not found
3.  multiple candidate matches (ambiguous path)
4.  invalid --set, --delete-key or --delete-section string (parse error in path or TOML_VALUE)
5.  output path unwritable
6.  duplicate patches to the same path in one invocation

# Corner cases
- Empty string values must be quoted in the TOML_VALUE: "".
- Keys with dots/spaces must use quoted keys in the path: '"a.b".c'.
- For arrays-of-tables, an index out of range is a "not found" error.
- If a target key exists multiple times due to invalid/duplicate TOML, refuse with error 4.
- If a key does not currently have an inline comment and a patch omits a comment, no comment is added.
- If an existing key is on a multi-line structure (e.g., a multi-line array), the inline comment behavior applies to the key’s logical line; if not representable inline, place the comment on the key’s line above.

# Examples
Change three fields and set a top comment:
```sh
  python3 patch_toml.py config.toml config_patched.toml \
    --set 'logger.stdout_level = 6 # disable' \
    --set 'device.report_address = ""' \
    --set 'device.some_array = [2, 3]' \
    --delete-key 'device.shadowing' \
    --delete-section 'simulators' \
    --top-comment 'Auto-patched by CI on build'
```
