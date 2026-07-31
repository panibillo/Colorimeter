# Step 4 — SSH keypair and login helper (optional)

**Status:** ✅ ready · **Optional** — password SSH from step 1 already works.

Two conveniences, both set up on your **local** machine. Neither is required for
the instrument to function; they make day-to-day access nicer and safer.

← [03_upload_firmware_db_venv.md](03_upload_firmware_db_venv.md) · Back to [master guide](../RPi_commissioning.md) · Next → [05_testing.md](05_testing.md)

---

## 4a — Passwordless key-based login

Generate an Ed25519 keypair on local, copy the public key to the Pi, and add a
`~/.ssh/config` alias so you can just type `ssh <host>`. Full walkthrough:

➡ **[ssh-key-setup-guide.md](ssh-key-setup-guide.md)**
*(local-only symlink into `~/Documents`; not present on fresh clones)*

Short version:

```bash
# On local:
ssh-keygen -t ed25519 -C "<pi-username>@$(hostname)"     # e.g. mgwwg@<local-host>
ssh-copy-id -i ~/.ssh/id_ed25519.pub <pi-username>@<hostname>.local

# Add an alias so `ssh <host>` just works:
cat >> ~/.ssh/config <<'EOF'

Host rpizero2
    HostName rpizero2.local     # or the DHCP-reserved IP
    User mgwwg                  # the Pi account you chose in step 1 (e.g. bill on rpizero1)
    IdentityFile ~/.ssh/id_ed25519
EOF
chmod 600 ~/.ssh/config
ssh rpizero2                     # logs in without a password
```

The `<host>` alias you set here is what the sync scripts and `ssh <host>`
commands throughout this guide use.

> **Optional hardening:** once key login works, you may disable password auth on
> the Pi (`PasswordAuthentication no` in `/etc/ssh/sshd_config`, then
> `sudo systemctl restart ssh`). Do this **only** after confirming key login —
> otherwise you can lock yourself out. Details in the linked guide, Step 6.

---

## 4b — Colored-terminal login helper (guards against wrong-machine mistakes)

A tiny local wrapper that tints the terminal background while you are SSH'd into
the instrument and restores it on exit — an instant visual "you are on the Pi"
cue. Full write-up:

➡ **[ssh-terminal-color.md](ssh-terminal-color.md)**
*(local-only symlink; describes the pattern for the NAS — adapt the `ssh ash`
line to `ssh rpizero2`)*

The pattern: a script in `~/.local/bin/` that emits an OSC color escape, opens
the SSH session, and restores the color when it returns. Copy it to, e.g.,
`~/.local/bin/ssh-cm`, point its `ssh` line at your `<host>` alias, and
`chmod +x` it.

---

Next → [05_testing.md](05_testing.md) — verify every peripheral, then run the
application.
