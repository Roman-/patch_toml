# patch_toml

Small utility to apply precise value changes to a TOML config file while preserving formatting and comments.

## Example

Patch a few settings in place while keeping the rest of the file untouched:

```sh
python3 patch_toml.py config.toml config_patched.toml \
  --set 'logger.stdout_level = 4' \
  --set 'device.report_address = ""'
```

The command rewrites only the targeted keys and leaves everything else exactly as it was.
