package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"github.com/Origami74/gonuts-tollgate/cashu"
	"github.com/Origami74/gonuts-tollgate/cashu/nuts/nut04"
	"github.com/Origami74/gonuts-tollgate/wallet"
)

const defaultMintURL = "https://testnut-compat.mints.orangesync.tech"
const defaultAmount = 1013
const feeBuffer = 10

func main() {
	mintURL := defaultMintURL
	amount := uint64(defaultAmount)
	if len(os.Args) > 1 {
		mintURL = os.Args[1]
	}
	if len(os.Args) > 2 {
		_, err := fmt.Sscanf(os.Args[2], "%d", &amount)
		if err != nil {
			log.Fatalf("invalid amount: %v", err)
		}
	}

	tmpDir, err := os.MkdirTemp("", "mint-token-*")
	if err != nil {
		log.Fatalf("temp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	cfg := wallet.Config{
		WalletPath:     tmpDir,
		CurrentMintURL: mintURL,
	}
	w, err := wallet.LoadWallet(cfg)
	if err != nil {
		log.Fatalf("LoadWallet: %v", err)
	}

	mintAmount := amount + feeBuffer

	quote, err := w.RequestMint(mintAmount, mintURL)
	if err != nil {
		w.Shutdown()
		log.Fatalf("RequestMint(%d, %s): %v", mintAmount, mintURL, err)
	}

	deadline := time.Now().Add(60 * time.Second)
	backoff := 500 * time.Millisecond
	const maxBackoff = 10 * time.Second
	for time.Now().Before(deadline) {
		state, err := w.MintQuoteState(quote.Quote)
		if err != nil {
			errStr := err.Error()
			if strings.Contains(errStr, "429") || strings.Contains(strings.ToLower(errStr), "rate limit") {
				log.Printf("mint rate-limited, backing off %v", backoff)
				time.Sleep(backoff)
				backoff = time.Duration(float64(backoff) * 2)
				if backoff > maxBackoff {
					backoff = maxBackoff
				}
				continue
			}
			w.Shutdown()
			log.Fatalf("MintQuoteState: %v", err)
		}
		if state.State == nut04.Paid {
			break
		}
		backoff = 500 * time.Millisecond
		time.Sleep(backoff)
	}

	minted, err := w.MintTokens(quote.Quote)
	if err != nil {
		w.Shutdown()
		log.Fatalf("MintTokens: %v", err)
	}

	balance := w.GetBalance()
	_ = minted

	sendResult, err := w.SendWithOptions(amount, mintURL, wallet.SendOptions{
		IncludeFees:            true,
		AllowOverpayment:       true,
		MaxOverpaymentPercent:  10000,
		MaxOverpaymentAbsolute: 500,
	})
	if err != nil {
		w.Shutdown()
		log.Fatalf("SendWithOptions: %v", err)
	}

	token, err := cashu.NewTokenV4(sendResult.Proofs, mintURL, cashu.Sat, true)
	if err != nil {
		w.Shutdown()
		log.Fatalf("NewTokenV4: %v", err)
	}

	tokenStr, err := token.Serialize()
	if err != nil {
		w.Shutdown()
		log.Fatalf("Serialize: %v", err)
	}

	if err := w.Shutdown(); err != nil {
		log.Printf("warning: shutdown: %v", err)
	}

	result := map[string]interface{}{
		"token":       tokenStr,
		"amount":      amount,
		"balance":     balance,
		"overpayment": sendResult.Overpayment,
	}
	out, _ := json.Marshal(result)
	fmt.Println(string(out))
}
