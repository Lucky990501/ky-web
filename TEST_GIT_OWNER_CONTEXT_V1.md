# TEST ONLY — fixed Git owner identity context

Application remains db8e23658baa6e4b707380e178aded561d3280f2 /
04b6774d9e7a95e87871ea9c2e360805988a8e13; no business Source modification.
This minimal tooling child of 85a1451 adds owner-context identity validation.
Root copies these verified Git blobs to a new Root-owned, read-only native
control directory and seals their SHA; never runs a mutable checkout script
with Root privilege. Old contracts/receipts/Runtime artifact/lock are untouched.

Only the two closed repository identities are accepted. /usr/bin/git and
/usr/bin/sudo are fixed, Root-owned executables; subprocess argv has no shell.
Git identity and the existing read-only runtime contract/resolve identity probe
execute after sudo drops to lucky (UID1000); lucky has no install authority.
No SUDO_UID spoofing or forwarding is required. No safe.directory overrides,
ownership changes, permission relaxation, network, install, Registry mutation
or Task/Provider execution is introduced.

Root verifies native seal, executable/module SHA, fixed repo owner/path, exact
UID/GID1000 and no world-write. Existing group-write is allowed only when the
lucky group has no additional explicit or primary-group users. Native v2
directory preserves the immutable initial pre-Scope attempt; no chmod/chown.
commit/tree, before/after inode identity, and the complete sealed installed
file map. It rechecks identity/content after the child proof and again before
formal activation. The child is Root-owned and read-only, runs only as lucky,
uses -I/-B and a fixed minimal environment, and imports only the frozen target
for its unchanged existing runtime resolver. Final installation and atomic
switch stay in the existing protected formal Test entry.
