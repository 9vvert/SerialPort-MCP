# A simple mcp server serves as bridge between AI and serial port

## Overview
Sometimes you want AI to execute commands inside a virtual machine. Tools like `vmrun` can run commands from the host, but they are awkward for interactive programs (for example gdb with its own CLI), and you cannot see live output. SerialPort-MCP solves this. After you add a serial port to your VM and enable a serial console inside the VM, this MCP exposes read and write services so your AI can interact with the VM in real time. This is not limited to running commands. You can let the AI log in, run commands, and watch output live, for example to debug a process, set breakpoints, and observe its behavior. (But you may need additional skills depending on your workflow)

## Step 1: Add a serial port to the VM
In VMware: `VMWare -> VM -> Settings -> Hardware`, then `Add`, choose `Serial Port`.

### Linux
Select `Socket`, example file name: `/tmp/citrix0`. Direction: `Server -> VM` (host acts as server, then use socat on the host to bind the socket to a PTY).

### Windows
Select `Named pipe`, example file name: `\\.\pipe\latest-citrix-vm-serial`. Direction: `This end is the server`, `The other end is an application`.

## Step 2: Enable serial console inside the VM
Example for FreeBSD-like systems:
For BSD systems, you usually edit `loader.conf`, which may be at `/boot/loader.conf` or `/flash/boot/loader.conf`.

Add these lines (adjust speed if needed):
```
boot_serial="YES"
comconsole="9600"
boot_multicons="YES"
console="comconsole,vidconsole"
```

## Step 3: Run SerialPort-MCP
### Linux
Create a PTY bridge using socat:
```shell
socat -d -d pty,raw,echo=0,link=/tmp/vmconsole UNIX-CONNECT:/tmp/citrix0
```

Then add SerialPort-MCP to your MCP config (example `~/codex/config.toml`):
```toml
[mcp_servers.SerialPortMCP]
type = "stdio"
command = "<PATH TO PYTHON>"
args = ["<Path to vmconsole_mcp.py>", "--pipe-style", "linux", "--tty-path", "/tmp/vmconsole"]
```
Change `--tty-path` to match your socat PTY path. Then restart codex.

### Windows
Add SerialPort-MCP to your MCP config (example `~/codex/config.toml`):
```toml
[mcp_servers.SerialPortMCP]
type = "stdio"
command = "<PATH TO PYTHON>"
args = [
  "<Path to vmconsole_mcp.py>",
  "--pipe-style", "windows",
  "--tty-path", "\\\\.\\pipe\\latest-citrix-vm-serial"
]
```
Change `--tty-path` to match your named pipe. Then restart codex.

## Tools
- `tty_status`query the current pty path and status
- `tty_set_path`switch pty path
- `tty_read`read data
  - args: max_bytes`(default: 4096), `timeout_ms`(default: 120), `encoding`(`utf-8`/`latin-1`/`hex`)
- `tty_write`write data
  - args: data`, `append_newline`(default: false), `encoding`(`utf-8`/`hex`)
- `tty_control` send control-character
  - args: `key` (`c-c`, `c-z`, `c-d`, `esc`, `tab`, `enter`, `return`, `lf`, `backspace`), `repeat`(optional)
