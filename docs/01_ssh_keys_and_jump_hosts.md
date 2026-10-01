# 1. SSH keys and jump hosts

Every interaction with a cluster -- logging in, copying inputs, fetching
results -- goes through SSH. Ten minutes spent on keys, an agent and a
`~/.ssh/config` file save a password prompt on every command and make scripts
such as `tools/fetch_runs.sh` possible at all.

## Why keys instead of passwords

* **Scripts can run unattended.** `rsync`, `scp` and `ssh` in a loop cannot
  type a password; with a key loaded in an agent they just work.
* **Safer.** The private key never leaves your machine; the server only keeps
  the public half. A stolen public key is useless.
* **Often mandatory.** Many HPC centres disable password login altogether.

## Why a jump host

Many clusters are not reachable from the internet: you first connect to a
*gateway* (jump host, bastion) and from there to the login node. Doing it in
two manual steps breaks every file transfer. OpenSSH's **ProxyJump** (`-J`)
makes the two hops look like one connection:

```text
your laptop  --ssh-->  gateway.example.org  --ssh-->  login.cluster.example.org
             \__________________ ssh -J ___________________/
```

The key stays on your laptop: the gateway only forwards the encrypted stream
(no need to copy your private key to the gateway -- never do that).

## Step 1 -- create a key pair

```bash
ssh-keygen -t ed25519 -C "laptop-2026"          # modern default
# some older systems still require RSA:
ssh-keygen -t rsa -b 4096 -C "laptop-2026"
```

Accept the default location (`~/.ssh/id_ed25519`) or give a descriptive name,
and **set a passphrase**: it encrypts the private key on disk. The agent (step 3)
means you type it once per session, not once per command.

One key per machine (laptop, lab PC) is good practice: if a machine is lost you
revoke only its key.

## Step 2 -- install the public key

```bash
# on the gateway
ssh-copy-id -i ~/.ssh/id_ed25519.pub user@gateway.example.org
# on the cluster, through the gateway
ssh-copy-id -i ~/.ssh/id_ed25519.pub -o ProxyJump=user@gateway.example.org user@login.cluster.example.org
```

`ssh-copy-id` appends the public key to `~/.ssh/authorized_keys` on the server.
Many HPC consortia instead ask you to upload the **public** key (`.pub`) through
a web portal; it then propagates to all their clusters. Read your centre's
documentation first.

## Step 3 -- load the key in an agent

```bash
eval "$(ssh-agent -s)"           # start an agent for this shell
ssh-add ~/.ssh/id_ed25519        # passphrase asked once
ssh-add -l                       # list the loaded keys
ssh-add -t 8h ~/.ssh/id_ed25519  # optional: forget it after 8 hours
```

`tools/fetch_runs.sh` checks `ssh-add -l` and reminds you when no key is
loaded (otherwise the passphrase is asked twice per connection with a jump
host: once per hop).

## Step 4 -- write `~/.ssh/config`

```text
# ~/.ssh/config  (Windows: C:\Users\<you>\.ssh\config)
Host gateway
    HostName gateway.example.org
    User user
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes

Host mycluster
    HostName login.cluster.example.org
    User user
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ProxyJump gateway
    ServerAliveInterval 60
    ServerAliveCountMax 5
```

Now every tool understands the short name:

```bash
ssh mycluster
scp INCAR mycluster:/path/to/runs/Si/
rsync -avzhP mycluster:/path/to/runs/ ./runs/
REMOTE=mycluster GATEWAY= bash tools/fetch_runs.sh campaign1 campaign1
```

What the options do:

| Option | Why |
| --- | --- |
| `ProxyJump gateway` | the two hops in one, for ssh, scp, rsync, sftp, VS Code Remote... |
| `IdentitiesOnly yes` | offer only this key (avoids "Too many authentication failures" when the agent holds many keys) |
| `ServerAliveInterval 60` | keep idle connections (long transfers, `tail -f`) alive through firewalls |

On Linux and macOS you can also reuse one connection for many commands
(`ControlMaster auto`, `ControlPath ~/.ssh/cm-%r@%h:%p`, `ControlPersist 10m`);
the Windows build of OpenSSH does not support it.

If your OpenSSH is older than 7.3 (no `ProxyJump`), use the equivalent
`ProxyCommand ssh -W %h:%p gateway`.

## Windows notes

There are four common ways to get SSH on Windows; they keep **separate**
key folders and configs, which explains most "it works in one terminal but not
in the other" problems.

| Environment | Home / keys | rsync? | Notes |
| --- | --- | --- | --- |
| Windows OpenSSH (PowerShell, cmd) | `C:\Users\<you>\.ssh\` | no | Built into Windows 10/11. Start the agent once as administrator: `Get-Service ssh-agent \| Set-Service -StartupType Automatic; Start-Service ssh-agent`, then `ssh-add`. |
| Git Bash | `/c/Users/<you>/.ssh/` (same files as above) | no | Ships its own `ssh`/`ssh-agent`; good for scripts, but no `rsync`. Drives are `/c`, `/e`. |
| MobaXterm (local terminal) | MobaXterm's own home (set a *persistent home directory* in the settings, or it is lost on exit) | yes | Drives are `/drives/c`, `/drives/e`. Its Cygwin-based `rsync` must be used with its own `ssh`. |
| WSL (Ubuntu on Windows) | `~/.ssh/` inside Linux | yes (`sudo apt install rsync`) | Windows drives are `/mnt/c`, `/mnt/e`. Keep the key in the Linux home: files on `/mnt/c` cannot get `chmod 600`, and ssh refuses keys with open permissions. |

`tools/fetch_runs.sh` translates `E:/data`-style paths to the right prefix for
each of these environments.

**"WARNING: UNPROTECTED PRIVATE KEY FILE!" with Windows OpenSSH.** The key file
must be readable by you only:

```powershell
icacls $env:USERPROFILE\.ssh\id_ed25519 /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

## Common errors

| Message | Likely cause and fix |
| --- | --- |
| `Permission denied (publickey)` | public key not installed on *that* host (gateway and cluster are separate), wrong `User`, or the agent does not hold the key (`ssh-add -l`). Debug with `ssh -v mycluster`. |
| `Too many authentication failures` | the agent offers many keys and the server gives up: add `IdentitiesOnly yes` and an explicit `IdentityFile`. |
| `UNPROTECTED PRIVATE KEY FILE` | permissions too open: `chmod 600 ~/.ssh/id_ed25519` (Linux/WSL/macOS) or the `icacls` line above (Windows). |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | the server's host key changed (reinstallation) -- or a real attack. Confirm with the centre's announcements, then `ssh-keygen -R login.cluster.example.org`. |
| `Could not open a connection to your authentication agent` | no agent in this shell: `eval "$(ssh-agent -s)"`. |
| `channel 0: open failed: administratively prohibited` | the gateway forbids forwarding to that host/port: check the login node name, or ask the centre whether the gateway allows ProxyJump. |
| connection drops during long transfers | `ServerAliveInterval 60`; rerun the same `rsync` command -- it resumes. |
| `Bad configuration option: proxyjump` | OpenSSH < 7.3: use `ProxyCommand ssh -W %h:%p gateway`. |

## Good habits

* Never copy a private key to a cluster or a gateway; never send it by e-mail.
* Use a passphrase and an agent, not an unencrypted key.
* Keep `fetch_runs.conf` and `~/.ssh/config` out of public repositories: they
  contain user names and host names (this repository's `.gitignore` excludes
  `fetch_runs.conf`).
