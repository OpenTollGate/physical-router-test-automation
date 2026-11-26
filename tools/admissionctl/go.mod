// admissionctl is a standalone command, not part of the PRTA Python tree.
// It reaches the admission layer through a directory replace so the module
// checkout under test is the one that is compiled: no copy, no vendored fork.
// The path is overridable for a different checkout:
//
//	go mod edit -replace github.com/OpenTollGate/tollgate-module-basic-go=$HOME/worktrees/tollgate-fips-exit-consumer/src
module github.com/OpenTollGate/physical-router-test-automation/tools/admissionctl

go 1.26.0

require github.com/OpenTollGate/tollgate-module-basic-go v0.0.0

require (
	github.com/ImVexed/fasturl v0.0.0-20230304231329-4e41488060f3 // indirect
	github.com/btcsuite/btcd/btcec/v2 v2.3.4 // indirect
	github.com/btcsuite/btcd/btcutil v1.1.6 // indirect
	github.com/btcsuite/btcd/chaincfg/chainhash v1.1.0 // indirect
	github.com/bytedance/sonic v1.13.2 // indirect
	github.com/bytedance/sonic/loader v0.2.4 // indirect
	github.com/cloudwego/base64x v0.1.5 // indirect
	github.com/coder/websocket v1.8.13 // indirect
	github.com/decred/dcrd/crypto/blake256 v1.1.0 // indirect
	github.com/decred/dcrd/dcrec/secp256k1/v4 v4.4.0 // indirect
	github.com/josharian/intern v1.0.0 // indirect
	github.com/json-iterator/go v1.1.12 // indirect
	github.com/klauspost/cpuid/v2 v2.2.10 // indirect
	github.com/mailru/easyjson v0.9.0 // indirect
	github.com/modern-go/concurrent v0.0.0-20180306012644-bacd9c7ef1dd // indirect
	github.com/modern-go/reflect2 v1.0.2 // indirect
	github.com/nbd-wtf/go-nostr v0.51.12 // indirect
	github.com/puzpuzpuz/xsync/v3 v3.5.1 // indirect
	github.com/tidwall/gjson v1.18.0 // indirect
	github.com/tidwall/match v1.1.1 // indirect
	github.com/tidwall/pretty v1.2.1 // indirect
	github.com/twitchyliquid64/golang-asm v0.15.1 // indirect
	golang.org/x/arch v0.17.0 // indirect
	golang.org/x/exp v0.0.0-20250506013437-ce4c2cf36ca6 // indirect
	golang.org/x/sys v0.48.0 // indirect
)

replace github.com/OpenTollGate/tollgate-module-basic-go => /home/c08r4d0r/worktrees/tollgate-fips-exit-consumer/src
