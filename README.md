# pty_mcp: a simple pseudo-terminal mcp server

## Example
In this example, I will let my LLM control the virtual machine opened in VMWare.

### 0x1 Create serial port in vmware
in top bar: VM -> Settings -> Hardware -> Serial Port, then create /tmp/xyz_serial_port, for instance.

### 0x2 Create pty using socat
```shell
socat -d -d pty,raw,echo=0,link=/tmp/vmconsole UNIX-CONNECT:/tmp/xyz_serial_port
```

### 0x3 Add pty-mcp to your llm config file
Take `~/codex/config.toml` as example (remember to replace it with real path):
```toml
[mcp_servers.pty_mcp]
type = "stdio"
command = "python"
args = ["<Path to vmconsole_mcp.py>", "--tty-path", "/tmp/vmconsole"]
```
Then restart codex.

## Tools
- `tty_status`：query the current pty path and status
- `tty_set_path`：switch pty path
- `tty_read`：read data
  - args：`max_bytes`(default: 4096), `timeout_ms`(default: 120), `encoding`(`utf-8`/`latin-1`/`hex`)
- `tty_write`：write data
  - args：`data`, `append_newline`(default: false), `encoding`(`utf-8`/`hex`)
- `tty_control`：send control-character
  - args: `key` (`c-c`, `c-z`, `c-d`, `esc`, `tab`, `enter`, `return`, `lf`, `backspace`), `repeat`(optional)
