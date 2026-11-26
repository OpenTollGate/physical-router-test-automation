// Command admissionctl drives the fips_exit admission layer's exported API
// with real files, and nothing else. It is the seam the module deliberately
// leaves to its caller:
//
//   - the Store implementation (temp file in the same directory, fsync,
//     rename, fsync of the directory),
//   - a clock (the module never reads one),
//   - SIGHUP to fips-exitd (deliberately NOT done here: the caller that owns
//     the daemon reloads it).
//
// Every decision below comes from fips_exit; this program only parses flags,
// moves bytes and prints. It holds no policy of its own.
//
// Subcommands:
//
//	admissionctl grant <64-hex-xonly-key> <ttl-seconds> <byte-budget>
//	admissionctl verdict <now-unix-epoch> <bytes-used>
//	admissionctl render
//	admissionctl reconcile <clients-file>
//
// State lives in $ADMISSIONCTL_DIR (default /var/lib/admissionctl). The
// clients file defaults to $ADMISSIONCTL_DIR/clients.
package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/OpenTollGate/tollgate-module-basic-go/fips_exit"
)

// ---------------------------------------------------------------------------
// Store: the module's persistence seam, backed by a real file.

// fileStore is a fips_exit.Store over one path. WriteAtomic follows the
// contract in the package docs: a temporary file in the target's own
// directory, fsync of its bytes, rename over the target, then fsync of the
// directory, so a crash leaves either the old bytes or the new ones.
type fileStore struct {
	path string
}

func (s fileStore) Read() ([]byte, error) {
	data, err := os.ReadFile(s.path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, fips_exit.ErrNoState
	}
	if err != nil {
		return nil, err
	}
	return data, nil
}

func (s fileStore) WriteAtomic(data []byte) error {
	return writeAtomic(s.path, data)
}

// writeAtomic is the same discipline for any target, used for the clients
// file too: the daemon must never read a half-written list.
func writeAtomic(path string, data []byte) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("create %s: %w", dir, err)
	}
	f, err := os.CreateTemp(dir, "."+filepath.Base(path)+".tmp-*")
	if err != nil {
		return fmt.Errorf("create temp file in %s: %w", dir, err)
	}
	tmp := f.Name()
	// On any failure the temporary file must not be left behind as litter.
	defer func() {
		if tmp != "" {
			_ = os.Remove(tmp)
		}
	}()
	if _, err := f.Write(data); err != nil {
		f.Close()
		return fmt.Errorf("write %s: %w", tmp, err)
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return fmt.Errorf("fsync %s: %w", tmp, err)
	}
	if err := f.Close(); err != nil {
		return fmt.Errorf("close %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		return fmt.Errorf("rename %s -> %s: %w", tmp, path, err)
	}
	tmp = "" // renamed: nothing to clean up
	d, err := os.Open(dir)
	if err != nil {
		return fmt.Errorf("open %s for fsync: %w", dir, err)
	}
	defer d.Close()
	if err := d.Sync(); err != nil {
		return fmt.Errorf("fsync directory %s: %w", dir, err)
	}
	return nil
}

// ---------------------------------------------------------------------------
// Paths

func dir() string {
	if d := os.Getenv("ADMISSIONCTL_DIR"); d != "" {
		return d
	}
	return "/var/lib/admissionctl"
}

func statePath() string   { return filepath.Join(dir(), "state.json") }
func clientsPath() string { return filepath.Join(dir(), "clients") }

// ---------------------------------------------------------------------------
// Helpers

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "admissionctl: "+format+"\n", args...)
	os.Exit(1)
}

func loadState() fips_exit.State {
	s, err := fips_exit.LoadState(fileStore{statePath()})
	if err != nil {
		fail("%v", err)
	}
	return s
}

func saveState(s fips_exit.State) {
	if err := fips_exit.SaveState(fileStore{statePath()}, s); err != nil {
		fail("%v", err)
	}
}

func writeClients(path string, rendered []byte) {
	if err := writeAtomic(path, rendered); err != nil {
		fail("%v", err)
	}
}

// printClients prints rendered bytes verbatim between markers, so a log
// carries exactly the bytes the daemon is given (including a trailing LF).
func printClients(rendered []byte) {
	fmt.Printf("clients-file-begin (%d bytes)\n", len(rendered))
	os.Stdout.Write(rendered)
	if len(rendered) == 0 || rendered[len(rendered)-1] != '\n' {
		fmt.Println()
	}
	fmt.Println("clients-file-end")
}

func parseUint(name, s string) uint64 {
	n, err := strconv.ParseUint(s, 10, 64)
	if err != nil {
		fail("%s must be a non-negative integer, got %q", name, s)
	}
	return n
}

// ---------------------------------------------------------------------------
// grant

// grant adds one entitlement, renders the clients file from the resulting
// state and writes both. Rendering goes through Apply so the file it writes
// is the layer's own output, not a second renderer.
func grant(args []string) {
	if len(args) != 3 {
		fail("usage: admissionctl grant <64-hex-xonly-key> <ttl-seconds> <byte-budget>")
	}
	key, err := fips_exit.ParsePeerKey(args[0])
	if err != nil {
		fail("%v", err)
	}
	ttl := parseUint("ttl-seconds", args[1])
	if ttl == 0 {
		fail("ttl-seconds must be greater than zero")
	}
	budget := parseUint("byte-budget", args[2])

	now := time.Now()
	mode := fips_exit.BillingTime
	if budget > 0 {
		mode = fips_exit.BillingBytes
	}
	state := loadState()
	next, err := fips_exit.Grant(state, fips_exit.GrantRequest{
		Key:        key,
		Name:       "admissionctl",
		Mode:       mode,
		GrantedAt:  now,
		ExpiresAt:  now.Add(time.Duration(ttl) * time.Second),
		ByteBudget: budget,
	})
	if err != nil {
		fail("%v", err)
	}
	applied, err := fips_exit.Apply(next, now, nil)
	if err != nil {
		fail("%v", err)
	}
	saveState(applied.State)
	writeClients(clientsPath(), applied.Rendered)

	outer, inner := fips_exit.Derive(key)
	ent := next.Entitlements[len(next.Entitlements)-1]
	fmt.Printf("key %s\n", key.Hex())
	fmt.Printf("outer %s\n", outer.String())
	// The inner address is the tunnel's IPv4 identity for this peer: the exit
	// derives it from the same key, and a caller needs it to route replies.
	fmt.Printf("inner %s\n", inner.String())
	fmt.Printf("mode %s ttl %ds byte_budget %d\n", mode, ttl, budget)
	fmt.Printf("expires_at %s\n", ent.ExpiresAt.UTC().Format(time.RFC3339))
	// The same instant as an epoch, so a caller never has to re-parse a
	// timestamp to ask for a verdict past it.
	fmt.Printf("expires_epoch %d\n", ent.ExpiresAt.Unix())
	fmt.Printf("revision %d\n", applied.State.Revision)
	printClients(applied.Rendered)
}

// ---------------------------------------------------------------------------
// verdict

// verdict reports the decision for each held grant at a caller-supplied moment
// and byte counter, then writes the clients file that decision implies.
// The counter is one number: it is applied to every held grant. The lane this
// serves holds exactly one grant at the moment it asks, so the number is
// unambiguously that peer's; a multi-peer caller would need one call per peer.
func verdict(args []string) {
	if len(args) != 2 {
		fail("usage: admissionctl verdict <now-unix-epoch> <bytes-used>")
	}
	nowUnix := parseUint("now-unix-epoch", args[0])
	bytesUsed := parseUint("bytes-used", args[1])
	now := time.Unix(int64(nowUnix), 0)

	state := loadState()
	observations := make([]fips_exit.Observation, 0, len(state.Entitlements))
	verdicts := make(map[string]fips_exit.Verdict, len(state.Entitlements))
	for _, ent := range state.Entitlements {
		observations = append(observations, fips_exit.Observation{Key: ent.Key, BytesUsed: bytesUsed})
		verdicts[ent.Key.Hex()] = fips_exit.VerdictFor(state, now, ent.Key, bytesUsed)
	}
	applied, err := fips_exit.Apply(state, now, observations)
	if err != nil {
		fail("%v", err)
	}
	saveState(applied.State)
	writeClients(clientsPath(), applied.Rendered)

	keys := make([]string, 0, len(verdicts))
	for k := range verdicts {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	if len(keys) == 0 {
		fmt.Println("verdict renew (no grant held)")
	}
	for _, k := range keys {
		fmt.Printf("verdict %s %s\n", verdicts[k], k)
	}
	fmt.Printf("revision %d\n", applied.State.Revision)
	printClients(applied.Rendered)
}

// ---------------------------------------------------------------------------
// render

// render prints the clients file the state implies at the current moment. It
// is a projection: nothing is written, so asking never changes an admission.
func render(args []string) {
	if len(args) != 0 {
		fail("usage: admissionctl render")
	}
	state := loadState()
	applied, err := fips_exit.Apply(state, time.Now(), nil)
	if err != nil {
		fail("%v", err)
	}
	printClients(applied.Rendered)
}

// ---------------------------------------------------------------------------
// reconcile

// reconcile folds the state and an observed clients file together: live grants
// keep their lines, revoked ones are dropped, and lines this layer does not own
// are preserved and reported. The reconciled file is written back over the
// observed path, because that is what the daemon reads.
func reconcile(args []string) {
	if len(args) != 1 {
		fail("usage: admissionctl reconcile <clients-file>")
	}
	path := args[0]
	data, err := os.ReadFile(path)
	if err != nil {
		fail("read %s: %v", path, err)
	}
	observed := fips_exit.ParseClientsFile(data)
	for _, bad := range observed.Invalid {
		fmt.Fprintf(os.Stderr, "admissionctl: %s:%d unparsable, skipped: %s (%s)\n",
			path, bad.Line, bad.Text, bad.Reason)
	}

	state := loadState()
	next, file, report, err := fips_exit.Reconcile(state, time.Now(), observed)
	if err != nil {
		fail("%v", err)
	}
	rendered, err := fips_exit.RenderClientsFile(file)
	if err != nil {
		fail("%v", err)
	}
	saveState(next)
	writeClients(path, rendered)

	fmt.Printf("revision %d\n", next.Revision)
	fmt.Printf("preserved-unmanaged %d\n", len(report.NeedsGrant))
	for _, e := range report.NeedsGrant {
		fmt.Printf("  preserved %s # %s\n", e.Addr, e.Name)
	}
	fmt.Printf("dropped-revoked %d\n", len(report.Revoked))
	for _, a := range report.Revoked {
		fmt.Printf("  dropped %s\n", a.String())
	}
	fmt.Printf("needs-grant %d\n", len(report.NeedsGrant))
	for _, e := range report.NeedsGrant {
		fmt.Printf("  needs-grant %s # %s\n", e.Addr, e.Name)
	}
	for _, c := range report.Conflicts {
		fmt.Printf("  conflict %s observed-name %q durable-name %q\n", c.Addr, c.ObservedName, c.DurableName)
	}
	printClients(rendered)
}

// ---------------------------------------------------------------------------

func main() {
	if len(os.Args) < 2 {
		fail("usage: admissionctl <grant|verdict|render|reconcile> ...")
	}
	switch cmd := os.Args[1]; cmd {
	case "grant":
		grant(os.Args[2:])
	case "verdict":
		verdict(os.Args[2:])
	case "render":
		render(os.Args[2:])
	case "reconcile":
		reconcile(os.Args[2:])
	default:
		fail("unknown subcommand %q (want grant, verdict, render or reconcile)", strings.TrimSpace(cmd))
	}
}
