use crate::types::*;
use std::collections::BTreeMap;
use std::time::Instant;

pub async fn audit_nut06(auditor: &MintAuditor) -> Vec<NutTestResult> {
    let mut results = Vec::new();
    let mut info_ok = false;

    // Test 1: GET /v1/info returns 200 with valid structure
    let start = Instant::now();
    match auditor.fetch_mint_info().await {
        Ok(info) => {
            info_ok = true;
            let mut detail = format!("name={:?}, version={:?}", info.name, info.version);

            // Check required fields per NUT-06
            let mut warnings = Vec::new();
            if info.name.is_none() { warnings.push("missing 'name'"); }
            if info.pubkey.is_none() { warnings.push("missing 'pubkey'"); }
            if info.version.is_none() { warnings.push("missing 'version'"); }
            if info.description.is_none() { warnings.push("missing 'description'"); }
            if info.nuts.is_none() { warnings.push("missing 'nuts' map"); }

            let status = if warnings.is_empty() {
                TestStatus::Pass
            } else {
                detail.push_str(&format!(" | warnings: {}", warnings.join(", ")));
                TestStatus::Warn
            };

            results.push(NutTestResult {
                nut: "NUT-06".into(),
                name: "GET /v1/info — structure validation".into(),
                status,
                detail,
                duration_ms: start.elapsed().as_millis() as u64,
            });
        }
        Err(e) => {
            results.push(NutTestResult {
                nut: "NUT-06".into(),
                name: "GET /v1/info — structure validation".into(),
                status: TestStatus::Fail,
                detail: format!("error: {}", e),
                duration_ms: start.elapsed().as_millis() as u64,
            });
        }
    }

    // Test 2: Verify NUT claims map is present and parseable
    if info_ok {
        let start = Instant::now();
        match auditor.fetch_mint_info().await {
            Ok(info) => {
                match info.nuts {
                    Some(nuts) => {
                        let nut_list: Vec<String> = nuts.keys().cloned().collect();
                        results.push(NutTestResult {
                            nut: "NUT-06".into(),
                            name: "GET /v1/info — NUT claims map".into(),
                            status: TestStatus::Pass,
                            detail: format!("claims {} NUTs: {}", nut_list.len(), nut_list.join(", ")),
                            duration_ms: start.elapsed().as_millis() as u64,
                        });
                    }
                    None => {
                        results.push(NutTestResult {
                            nut: "NUT-06".into(),
                            name: "GET /v1/info — NUT claims map".into(),
                            status: TestStatus::Fail,
                            detail: "'nuts' field is null or missing".into(),
                            duration_ms: start.elapsed().as_millis() as u64,
                        });
                    }
                }
            }
            Err(e) => {
                results.push(NutTestResult {
                    nut: "NUT-06".into(),
                    name: "GET /v1/info — NUT claims map".into(),
                    status: TestStatus::Fail,
                    detail: format!("error: {}", e),
                    duration_ms: start.elapsed().as_millis() as u64,
                });
            }
        }
    }

    results
}

pub async fn audit_nut01(auditor: &MintAuditor) -> Vec<NutTestResult> {
    let mut results = Vec::new();

    // Test 1: GET /v1/keysets returns valid keyset list
    let start = Instant::now();
    let keysets = match auditor.fetch_keysets().await {
        Ok(ks) => {
            let active: Vec<_> = ks.keysets.iter().filter(|k| k.active).collect();
            results.push(NutTestResult {
                nut: "NUT-01".into(),
                name: "GET /v1/keysets — keyset list".into(),
                status: TestStatus::Pass,
                detail: format!("{} keysets ({} active): {:?}", ks.keysets.len(), active.len(),
                    active.iter().map(|k| format!("{}({})", k.id, k.unit)).collect::<Vec<_>>().join(", ")),
                duration_ms: start.elapsed().as_millis() as u64,
            });
            ks
        }
        Err(e) => {
            results.push(NutTestResult {
                nut: "NUT-01".into(),
                name: "GET /v1/keysets — keyset list".into(),
                status: TestStatus::Fail,
                detail: format!("error: {}", e),
                duration_ms: start.elapsed().as_millis() as u64,
            });
            return results;
        }
    };

    // Test 2: Validate keyset ID formats (V1 vs V2)
    let start = Instant::now();
    let mut v1_count = 0;
    let mut v2_count = 0;
    let mut invalid_count = 0;

    for ks in &keysets.keysets {
        let id = &ks.id;
        if id.len() == 16 && id.starts_with("00") {
            v1_count += 1;
        } else if id.len() == 66 && id.starts_with("01") {
            v2_count += 1;
        } else if id.len() == 16 {
            // V1 without 00 prefix (some mints)
            v1_count += 1;
        } else {
            invalid_count += 1;
        }
    }

    let status = if invalid_count > 0 {
        TestStatus::Warn
    } else {
        TestStatus::Pass
    };

    results.push(NutTestResult {
        nut: "NUT-01".into(),
        name: "Keyset ID format validation (V1 vs V2)".into(),
        status,
        detail: format!("V1 IDs: {}, V2 IDs: {}, invalid: {}", v1_count, v2_count, invalid_count),
        duration_ms: start.elapsed().as_millis() as u64,
    });

    // Test 3: Fetch actual keys for each active keyset
    for ks in keysets.keysets.iter().filter(|k| k.active) {
        let start = Instant::now();
        match auditor.fetch_keys(&ks.id).await {
            Ok(keys_resp) => {
                let keyset_keys = &keys_resp.keysets[0];
                let num_keys = keyset_keys.keys.len();
                let status = if num_keys > 0 {
                    TestStatus::Pass
                } else {
                    TestStatus::Fail
                };
                results.push(NutTestResult {
                    nut: "NUT-01".into(),
                    name: format!("GET /v1/keysets/{} — public keys", ks.id),
                    status,
                    detail: format!("{} public keys for keyset {} ({})", num_keys, ks.id, ks.unit),
                    duration_ms: start.elapsed().as_millis() as u64,
                });
            }
            Err(e) => {
                results.push(NutTestResult {
                    nut: "NUT-01".into(),
                    name: format!("GET /v1/keysets/{} — public keys", ks.id),
                    status: TestStatus::Fail,
                    detail: format!("error: {}", e),
                    duration_ms: start.elapsed().as_millis() as u64,
                });
            }
        }
    }

    results
}

pub async fn audit_nut04(auditor: &MintAuditor) -> Vec<NutTestResult> {
    let mut results = Vec::new();

    // Test 1: Create a mint quote for 1 sat
    let start = Instant::now();
    let quote = match auditor.create_mint_quote(1, "sat").await {
        Ok(q) => {
            let quote_id = q.get("quote").and_then(|v| v.as_str()).unwrap_or("?");
            let request = q.get("request").and_then(|v| v.as_str()).unwrap_or("?");
            let state = q.get("state").and_then(|v| v.as_str()).unwrap_or("?");
            let amount = q.get("amount").and_then(|v| v.as_u64()).unwrap_or(0);

            let mut warnings: Vec<String> = Vec::new();
            if quote_id == "?" { warnings.push("missing 'quote' field".into()); }
            if request == "?" { warnings.push("missing 'request' (bolt11) field".into()); }
            if amount != 1 { warnings.push(format!("amount={} expected 1", amount)); }

            let valid_states = ["UNPAID", "PAID", "ISSUED", "PENDING"];
            if !valid_states.contains(&state) {
                warnings.push(format!("state='{}' not in {:?}", state, valid_states));
            }

            let status = if warnings.is_empty() {
                TestStatus::Pass
            } else {
                TestStatus::Warn
            };

            results.push(NutTestResult {
                nut: "NUT-04".into(),
                name: "POST /v1/mint/quote/bolt11 — quote creation".into(),
                status,
                detail: format!("quote={} state={} amount={} bolt11={}... warnings: {}",
                    quote_id, state, amount, &request[..request.len().min(30).max(1)], warnings.join("; ")),
                duration_ms: start.elapsed().as_millis() as u64,
            });

            q
        }
        Err(e) => {
            results.push(NutTestResult {
                nut: "NUT-04".into(),
                name: "POST /v1/mint/quote/bolt11 — quote creation".into(),
                status: TestStatus::Fail,
                detail: format!("error: {}", e),
                duration_ms: start.elapsed().as_millis() as u64,
            });
            return results;
        }
    };

    // Test 2: Check quote state via GET
    if let Some(quote_id) = quote.get("quote").and_then(|v| v.as_str()) {
        let start = Instant::now();
        match auditor.check_mint_quote_state(quote_id).await {
            Ok(state_resp) => {
                let state = state_resp.get("state").and_then(|v| v.as_str()).unwrap_or("?");
                results.push(NutTestResult {
                    nut: "NUT-04".into(),
                    name: "GET /v1/mint/quote/bolt11/{id} — state check".into(),
                    status: TestStatus::Pass,
                    detail: format!("quote={} state={}", quote_id, state),
                    duration_ms: start.elapsed().as_millis() as u64,
                });
            }
            Err(e) => {
                results.push(NutTestResult {
                    nut: "NUT-04".into(),
                    name: "GET /v1/mint/quote/bolt11/{id} — state check".into(),
                    status: TestStatus::Fail,
                    detail: format!("error: {}", e),
                    duration_ms: start.elapsed().as_millis() as u64,
                });
            }
        }
    }

    // Test 3: Extended field validation per latest NUT-04 spec
    let start = Instant::now();
    let mut extended_warnings: Vec<String> = Vec::new();

    let has_amount_paid = quote.get("amount_paid").is_some();
    let has_amount_issued = quote.get("amount_issued").is_some();
    let has_updated_at = quote.get("updated_at").is_some();

    if !has_amount_paid {
        extended_warnings.push("missing 'amount_paid' (required per latest spec)".into());
    }
    if !has_amount_issued {
        extended_warnings.push("missing 'amount_issued' (required per latest spec)".into());
    }
    if !has_updated_at {
        extended_warnings.push("missing 'updated_at' (required per latest spec)".into());
    }

    let state = quote.get("state").and_then(|v| v.as_str()).unwrap_or("");
    let valid_mint_states = ["UNPAID", "PAID", "ISSUED"];
    if !valid_mint_states.contains(&state) {
        extended_warnings.push(format!(
            "state='{}' invalid for mint quote (UNPAID/PAID/ISSUED; PENDING is melt-only)",
            state
        ));
    }

    if let Some(qid) = quote.get("quote").and_then(|v| v.as_str()) {
        let looks_uuidv7 = qid.len() == 36
            && qid.as_bytes().get(14) == Some(&b'7')
            && qid.matches('-').count() == 4;
        if !looks_uuidv7 {
            extended_warnings.push(format!(
                "quote id '{}' not UUIDv7 (recommended, not required)",
                qid
            ));
        }
    }

    let status = if extended_warnings.is_empty() {
        TestStatus::Pass
    } else {
        TestStatus::Warn
    };
    results.push(NutTestResult {
        nut: "NUT-04".into(),
        name: "Mint quote extended fields (amount_paid/amount_issued/updated_at)".into(),
        status,
        detail: format!(
            "amount_paid={} amount_issued={} updated_at={} | warnings: {}",
            has_amount_paid, has_amount_issued, has_updated_at,
            extended_warnings.join("; ")
        ),
        duration_ms: start.elapsed().as_millis() as u64,
    });

    results
}

pub async fn audit_nut07(auditor: &MintAuditor) -> Vec<NutTestResult> {
    let mut results = Vec::new();

    // Test: POST /v1/checkstate with a dummy Y value
    // We test that the endpoint exists and returns a valid response structure
    let start = Instant::now();
    let dummy_y = "02".to_string() + &"0".repeat(64); // 02 + 64 zeros = invalid but well-formed
    match auditor.check_proof_state(&[dummy_y]).await {
        Ok(resp) => {
            let states = resp.get("states").and_then(|v| v.as_array());
            match states {
                Some(arr) => {
                    results.push(NutTestResult {
                        nut: "NUT-07".into(),
                        name: "POST /v1/checkstate — response structure".into(),
                        status: TestStatus::Pass,
                        detail: format!("returned {} states", arr.len()),
                        duration_ms: start.elapsed().as_millis() as u64,
                    });
                }
                None => {
                    results.push(NutTestResult {
                        nut: "NUT-07".into(),
                        name: "POST /v1/checkstate — response structure".into(),
                        status: TestStatus::Fail,
                        detail: "missing 'states' array in response".into(),
                        duration_ms: start.elapsed().as_millis() as u64,
                    });
                }
            }
        }
        Err(e) => {
            results.push(NutTestResult {
                nut: "NUT-07".into(),
                name: "POST /v1/checkstate — response structure".into(),
                status: TestStatus::Fail,
                detail: format!("error: {}", e),
                duration_ms: start.elapsed().as_millis() as u64,
            });
        }
    }

    results
}

pub async fn audit_nut19(auditor: &MintAuditor) -> Vec<NutTestResult> {
    let mut results = Vec::new();

    // Test: Check for cache headers on mint info endpoint
    let start = Instant::now();
    match auditor.fetch_text("/v1/info").await {
        Ok(resp) => {
            let etag = resp.headers().get("etag").is_some();
            let last_modified = resp.headers().get("last-modified").is_some();
            let cache_control = resp.headers().get("cache-control").is_some();

            let status = if etag || last_modified || cache_control {
                TestStatus::Pass
            } else {
                TestStatus::Warn
            };

            results.push(NutTestResult {
                nut: "NUT-19".into(),
                name: "Cache headers on GET /v1/info".into(),
                status,
                detail: format!("etag={} last_modified={} cache_control={}", etag, last_modified, cache_control),
                duration_ms: start.elapsed().as_millis() as u64,
            });
        }
        Err(e) => {
            results.push(NutTestResult {
                nut: "NUT-19".into(),
                name: "Cache headers on GET /v1/info".into(),
                status: TestStatus::Fail,
                detail: format!("error: {}", e),
                duration_ms: start.elapsed().as_millis() as u64,
            });
        }
    }

    results
}

pub async fn audit_nut02(auditor: &MintAuditor) -> Vec<NutTestResult> {
    let mut results = Vec::new();

    let start = Instant::now();
    let keysets = match auditor.fetch_keysets().await {
        Ok(ks) => ks,
        Err(e) => {
            results.push(NutTestResult {
                nut: "NUT-02".into(),
                name: "Keyset ID version classification (V1/V2/V3)".into(),
                status: TestStatus::Fail,
                detail: format!("error fetching keysets: {}", e),
                duration_ms: start.elapsed().as_millis() as u64,
            });
            return results;
        }
    };

    let mut by_version: BTreeMap<u64, Vec<String>> = BTreeMap::new();
    let mut unversioned: Vec<String> = Vec::new();
    for ks in &keysets.keysets {
        match ks.version {
            Some(v) => by_version.entry(v).or_default().push(ks.id.clone()),
            None => {
                if ks.id.starts_with("00") && ks.id.len() == 16 {
                    by_version.entry(1).or_default().push(ks.id.clone());
                } else if ks.id.starts_with("01") && ks.id.len() == 66 {
                    by_version.entry(2).or_default().push(ks.id.clone());
                } else if ks.id.starts_with("02") && ks.id.len() == 66 {
                    by_version.entry(3).or_default().push(ks.id.clone());
                } else {
                    unversioned.push(ks.id.clone());
                }
            }
        }
    }

    let v1 = by_version.get(&1).cloned().unwrap_or_default();
    let v2 = by_version.get(&2).cloned().unwrap_or_default();
    let v3 = by_version.get(&3).cloned().unwrap_or_default();
    let other: Vec<String> = by_version.iter()
        .filter(|(k, _)| **k > 3)
        .flat_map(|(_, v): (&u64, &Vec<String>)| v.clone())
        .collect();

    let status = if !v1.is_empty() || !unversioned.is_empty() || !other.is_empty() {
        TestStatus::Warn
    } else {
        TestStatus::Pass
    };

    results.push(NutTestResult {
        nut: "NUT-02".into(),
        name: "Keyset ID version classification (V1/V2/V3)".into(),
        status,
        detail: format!(
            "V1 (deprecated): {} [{}]; V2 (current): {} [{}]; V3 (BLS12-381): {} [{}]; other: {} [{}]; unknown/legacy: {} [{}]",
            v1.len(), v1.join(","),
            v2.len(), v2.join(","),
            v3.len(), v3.join(","),
            other.len(), other.join(","),
            unversioned.len(), unversioned.join(","),
        ),
        duration_ms: start.elapsed().as_millis() as u64,
    });

    let start = Instant::now();
    let active: Vec<_> = keysets.keysets.iter().filter(|k| k.active).collect();
    let with_fee: Vec<_> = active.iter().filter(|k| k.input_fee_ppk.is_some()).collect();
    let without_fee: Vec<_> = active.iter().filter(|k| k.input_fee_ppk.is_none()).collect();

    let status = if active.is_empty() {
        TestStatus::Warn
    } else if without_fee.is_empty() {
        TestStatus::Pass
    } else {
        TestStatus::Warn
    };

    results.push(NutTestResult {
        nut: "NUT-02".into(),
        name: "input_fee_ppk on active keysets".into(),
        status,
        detail: format!(
            "{} active keysets; {} expose input_fee_ppk, {} omit it",
            active.len(),
            with_fee.len(),
            without_fee.len(),
        ),
        duration_ms: start.elapsed().as_millis() as u64,
    });

    results
}

pub async fn audit_nut05(auditor: &MintAuditor) -> Vec<NutTestResult> {
    let mut results = Vec::new();
    let start = Instant::now();

    // Intentionally fake invoice: never melt real tokens.
    let dummy_invoice = "lnbc1dummy";
    match auditor.create_melt_quote_raw(dummy_invoice, "sat").await {
        Ok(resp) => {
            let code = resp.status();
            if code.is_success() {
                match resp.json::<serde_json::Value>().await {
                    Ok(body) => {
                        let quote = body.get("quote").and_then(|v| v.as_str()).unwrap_or("?");
                        let amount = body.get("amount").and_then(|v| v.as_u64());
                        let fee_reserve = body.get("fee_reserve").and_then(|v| v.as_u64());
                        let state = body.get("state").and_then(|v| v.as_str()).unwrap_or("?");

                        let mut warnings = Vec::new();
                        if amount.is_none() { warnings.push("missing 'amount'".into()); }
                        if fee_reserve.is_none() { warnings.push("missing 'fee_reserve'".into()); }
                        let valid_states = ["UNPAID", "PENDING", "PAID"];
                        if !valid_states.contains(&state) {
                            warnings.push(format!("state='{}'", state));
                        }

                        let status = if warnings.is_empty() { TestStatus::Pass } else { TestStatus::Warn };
                        results.push(NutTestResult {
                            nut: "NUT-05".into(),
                            name: "POST /v1/melt/quote/bolt11 — melt quote".into(),
                            status,
                            detail: format!(
                                "quote={} amount={:?} fee_reserve={:?} state={} | {}",
                                quote, amount, fee_reserve, state, warnings.join("; ")
                            ),
                            duration_ms: start.elapsed().as_millis() as u64,
                        });
                    }
                    Err(e) => {
                        results.push(NutTestResult {
                            nut: "NUT-05".into(),
                            name: "POST /v1/melt/quote/bolt11 — melt quote".into(),
                            status: TestStatus::Fail,
                            detail: format!("200 OK but invalid JSON: {}", e),
                            duration_ms: start.elapsed().as_millis() as u64,
                        });
                    }
                }
            } else {
                let body = resp.text().await.unwrap_or_default();
                let snippet = &body[..body.len().min(120)];
                // 4xx for a bogus invoice is valid: endpoint exists and validates input.
                let status = if code.is_client_error() { TestStatus::Pass } else { TestStatus::Warn };
                results.push(NutTestResult {
                    nut: "NUT-05".into(),
                    name: "POST /v1/melt/quote/bolt11 — melt quote".into(),
                    status,
                    detail: format!("dummy invoice rejected with {}: {}...", code, snippet),
                    duration_ms: start.elapsed().as_millis() as u64,
                });
            }
        }
        Err(e) => {
            results.push(NutTestResult {
                nut: "NUT-05".into(),
                name: "POST /v1/melt/quote/bolt11 — melt quote".into(),
                status: TestStatus::Fail,
                detail: format!("error: {}", e),
                duration_ms: start.elapsed().as_millis() as u64,
            });
        }
    }

    results
}

pub async fn audit_nut08(auditor: &MintAuditor) -> Vec<NutTestResult> {
    let mut results = Vec::new();
    let start = Instant::now();

    let keysets = match auditor.fetch_keysets().await {
        Ok(ks) => ks,
        Err(e) => {
            results.push(NutTestResult {
                nut: "NUT-08".into(),
                name: "input_fee_ppk reporting (swap fees)".into(),
                status: TestStatus::Fail,
                detail: format!("error: {}", e),
                duration_ms: start.elapsed().as_millis() as u64,
            });
            return results;
        }
    };

    let active: Vec<_> = keysets.keysets.iter().filter(|k| k.active).collect();
    let mut fee_lines = Vec::new();
    let mut total_ppk = 0u64;
    let mut zero_fee = 0usize;
    let mut all_reported = true;

    for ks in &active {
        match ks.input_fee_ppk {
            Some(fee) => {
                total_ppk += fee;
                if fee == 0 { zero_fee += 1; }
                fee_lines.push(format!("{}={}ppk", ks.id, fee));
            }
            None => {
                all_reported = false;
                fee_lines.push(format!("{}=<missing>", ks.id));
            }
        }
    }

    let status = if active.is_empty() {
        TestStatus::Warn
    } else if all_reported {
        TestStatus::Pass
    } else {
        TestStatus::Warn
    };

    results.push(NutTestResult {
        nut: "NUT-08".into(),
        name: "input_fee_ppk reporting (swap fees)".into(),
        status,
        detail: format!(
            "{} active keysets | {} | {} with 0 ppk | sum={}ppk",
            active.len(),
            fee_lines.join(", "),
            zero_fee,
            total_ppk,
        ),
        duration_ms: start.elapsed().as_millis() as u64,
    });

    results
}

pub async fn audit_nut09(auditor: &MintAuditor) -> Vec<NutTestResult> {
    let mut results = Vec::new();
    let start = Instant::now();

    match auditor.restore_raw(serde_json::Value::Array(vec![])).await {
        Ok(resp) => {
            let code = resp.status();
            if code.is_success() {
                match resp.json::<serde_json::Value>().await {
                    Ok(body) => {
                        let promises = body.get("promises").and_then(|v| v.as_array()).map(|a| a.len());
                        let signatures = body.get("signatures").and_then(|v| v.as_array()).map(|a| a.len());
                        results.push(NutTestResult {
                            nut: "NUT-09".into(),
                            name: "POST /v1/restore — endpoint check".into(),
                            status: TestStatus::Pass,
                            detail: format!(
                                "200 OK; promises={:?} signatures={:?}",
                                promises, signatures
                            ),
                            duration_ms: start.elapsed().as_millis() as u64,
                        });
                    }
                    Err(e) => {
                        results.push(NutTestResult {
                            nut: "NUT-09".into(),
                            name: "POST /v1/restore — endpoint check".into(),
                            status: TestStatus::Fail,
                            detail: format!("200 OK but invalid JSON: {}", e),
                            duration_ms: start.elapsed().as_millis() as u64,
                        });
                    }
                }
            } else {
                let body = resp.text().await.unwrap_or_default();
                let snippet = &body[..body.len().min(120)];
                let status = if code.is_client_error() { TestStatus::Pass } else { TestStatus::Warn };
                results.push(NutTestResult {
                    nut: "NUT-09".into(),
                    name: "POST /v1/restore — endpoint check".into(),
                    status,
                    detail: format!(
                        "endpoint responded {} (empty outputs rejected): {}...",
                        code, snippet
                    ),
                    duration_ms: start.elapsed().as_millis() as u64,
                });
            }
        }
        Err(e) => {
            results.push(NutTestResult {
                nut: "NUT-09".into(),
                name: "POST /v1/restore — endpoint check".into(),
                status: TestStatus::Fail,
                detail: format!("error: {}", e),
                duration_ms: start.elapsed().as_millis() as u64,
            });
        }
    }

    results
}

pub async fn audit_nut17(auditor: &MintAuditor, mint_info: Option<&MintInfo>) -> Vec<NutTestResult> {
    let mut results = Vec::new();

    let info = match mint_info {
        Some(i) => Some(i.clone()),
        None => auditor.fetch_mint_info().await.ok(),
    };
    let claimed = info
        .as_ref()
        .and_then(|i| i.nuts.as_ref())
        .map(|n| n.contains_key("17"))
        .unwrap_or(false);

    if !claimed {
        results.push(NutTestResult {
            nut: "NUT-17".into(),
            name: "NUT-17 — skipped".into(),
            status: TestStatus::Skip,
            detail: "mint does not claim NUT-17".into(),
            duration_ms: 0,
        });
        return results;
    }

    let start = Instant::now();
    match auditor.subscribe_check().await {
        Ok(resp) => {
            let code = resp.status();
            let upgrade = resp.headers().get("upgrade").is_some();
            let status = match code.as_u16() {
                101 | 200 | 400 | 426 => TestStatus::Pass,
                404 => TestStatus::Fail,
                _ => TestStatus::Warn,
            };
            results.push(NutTestResult {
                nut: "NUT-17".into(),
                name: "GET /v1/subscribe — WebSocket endpoint".into(),
                status,
                detail: format!("status={} upgrade_header={}", code, upgrade),
                duration_ms: start.elapsed().as_millis() as u64,
            });
        }
        Err(e) => {
            results.push(NutTestResult {
                nut: "NUT-17".into(),
                name: "GET /v1/subscribe — WebSocket endpoint".into(),
                status: TestStatus::Fail,
                detail: format!("error: {}", e),
                duration_ms: start.elapsed().as_millis() as u64,
            });
        }
    }

    results
}

pub async fn audit_nut20(auditor: &MintAuditor, mint_info: Option<&MintInfo>) -> Vec<NutTestResult> {
    let mut results = Vec::new();

    let info = match mint_info {
        Some(i) => Some(i.clone()),
        None => auditor.fetch_mint_info().await.ok(),
    };
    let claimed = info
        .as_ref()
        .and_then(|i| i.nuts.as_ref())
        .map(|n| n.contains_key("20"))
        .unwrap_or(false);

    if !claimed {
        results.push(NutTestResult {
            nut: "NUT-20".into(),
            name: "NUT-20 — skipped".into(),
            status: TestStatus::Skip,
            detail: "mint does not claim NUT-20".into(),
            duration_ms: 0,
        });
        return results;
    }

    let start = Instant::now();
    match auditor.create_mint_quote(1, "sat").await {
        Ok(quote) => {
            let pubkey = quote.get("pubkey").and_then(|v| v.as_str());
            let status = match pubkey {
                Some(pk) => {
                    let valid_hex = pk.len() == 66 && pk.chars().all(|c| c.is_ascii_hexdigit());
                    if valid_hex { TestStatus::Pass } else { TestStatus::Warn }
                }
                None => TestStatus::Fail,
            };
            results.push(NutTestResult {
                nut: "NUT-20".into(),
                name: "Mint quote pubkey field (NUT-20)".into(),
                status,
                detail: match pubkey {
                    Some(pk) => format!("pubkey present (len={}, valid_hex_66={})", pk.len(), pk.len() == 66),
                    None => "NUT-20 claimed but 'pubkey' missing from mint quote response".into(),
                },
                duration_ms: start.elapsed().as_millis() as u64,
            });
        }
        Err(e) => {
            results.push(NutTestResult {
                nut: "NUT-20".into(),
                name: "Mint quote pubkey field (NUT-20)".into(),
                status: TestStatus::Fail,
                detail: format!("error creating quote: {}", e),
                duration_ms: start.elapsed().as_millis() as u64,
            });
        }
    }

    results
}

pub async fn audit_nut29(auditor: &MintAuditor, mint_info: Option<&MintInfo>) -> Vec<NutTestResult> {
    let mut results = Vec::new();

    let info = match mint_info {
        Some(i) => Some(i.clone()),
        None => auditor.fetch_mint_info().await.ok(),
    };
    let nuts = info.as_ref().and_then(|i| i.nuts.as_ref());
    let claimed = nuts.map(|n| n.contains_key("29")).unwrap_or(false);

    if !claimed {
        results.push(NutTestResult {
            nut: "NUT-29".into(),
            name: "NUT-29 — skipped".into(),
            status: TestStatus::Skip,
            detail: "mint does not claim NUT-29".into(),
            duration_ms: 0,
        });
        return results;
    }

    let start = Instant::now();
    let nut29_val = nuts.and_then(|n| n.get("29"));
    let max_batch = nut29_val
        .and_then(|v| v.get("max_batch_size"))
        .and_then(|v| v.as_u64());

    let (status, detail) = match max_batch {
        Some(mbs) => (
            if mbs > 0 { TestStatus::Pass } else { TestStatus::Warn },
            format!("claims NUT-29 with max_batch_size={}", mbs),
        ),
        None => (
            TestStatus::Warn,
            format!("claims NUT-29 but 'max_batch_size' missing: {:?}", nut29_val),
        ),
    };
    results.push(NutTestResult {
        nut: "NUT-29".into(),
        name: "Batch mint support (NUT-29)".into(),
        status,
        detail,
        duration_ms: start.elapsed().as_millis() as u64,
    });

    results
}
