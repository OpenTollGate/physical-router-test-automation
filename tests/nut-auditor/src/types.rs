use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// NUT-06: Mint info response from GET /v1/info
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MintInfo {
    pub name: Option<String>,
    pub pubkey: Option<String>,
    pub version: Option<String>,
    pub description: Option<String>,
    pub description_long: Option<String>,
    pub contact: Option<Vec<ContactInfo>>,
    pub nuts: Option<BTreeMap<String, serde_json::Value>>,
    pub icon_url: Option<String>,
    pub time: Option<u64>,
    pub motd: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContactInfo {
    pub method: String,
    pub info: String,
}

/// NUT-01: Keyset response from GET /v1/keysets
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KeysetsResponse {
    pub keysets: Vec<KeysetInfo>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KeysetInfo {
    pub id: String,
    pub unit: String,
    pub active: bool,
    #[serde(rename = "input_fee_ppk")]
    pub input_fee_ppk: Option<u64>,
    /// NUT-02 keyset version (1=deprecated Secp256k1, 2=current, 3=BLS12-381)
    pub version: Option<u64>,
    pub max_order: Option<u64>,
    pub created_at: Option<u64>,
}

/// NUT-01: Keys for a specific keyset from GET /v1/keysets/{id}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KeysResponse {
    pub keysets: Vec<KeysetKeys>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KeysetKeys {
    pub id: String,
    pub unit: String,
    pub keys: BTreeMap<String, String>,
}

/// Individual test result
#[derive(Debug, Clone, Serialize)]
pub struct NutTestResult {
    pub nut: String,
    pub name: String,
    pub status: TestStatus,
    pub detail: String,
    pub duration_ms: u64,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub enum TestStatus {
    Pass,
    Fail,
    Skip,
    Warn,
}

/// Full audit report for a mint
#[derive(Debug, Clone, Serialize)]
pub struct AuditReport {
    pub mint_url: String,
    pub timestamp: String,
    pub mint_info: Option<MintInfo>,
    pub claimed_nuts: Vec<String>,
    pub results: Vec<NutTestResult>,
    pub summary: AuditSummary,
}

#[derive(Debug, Clone, Serialize)]
pub struct AuditSummary {
    pub total: usize,
    pub passed: usize,
    pub failed: usize,
    pub skipped: usize,
    pub warnings: usize,
}

impl AuditSummary {
    pub fn from_results(results: &[NutTestResult]) -> Self {
        let mut s = Self { total: results.len(), passed: 0, failed: 0, skipped: 0, warnings: 0 };
        for r in results {
            match r.status {
                TestStatus::Pass => s.passed += 1,
                TestStatus::Fail => s.failed += 1,
                TestStatus::Skip => s.skipped += 1,
                TestStatus::Warn => s.warnings += 1,
            }
        }
        s
    }
}

pub struct MintAuditor {
    pub client: reqwest::Client,
    pub mint_url: String,
}

impl MintAuditor {
    pub fn new(mint_url: &str) -> Self {
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(15))
            .build()
            .expect("failed to build HTTP client");
        Self {
            client,
            mint_url: mint_url.trim_end_matches('/').to_string(),
        }
    }

    async fn get(&self, path: &str) -> Result<reqwest::Response> {
        let url = format!("{}{}", self.mint_url, path);
        let resp = self.client.get(&url).send().await?;
        Ok(resp)
    }

    async fn get_json<T: for<'de> Deserialize<'de>>(&self, path: &str) -> Result<T> {
        let resp = self.get(path).await?;
        if !resp.status().is_success() {
            anyhow::bail!("GET {} returned {}", path, resp.status());
        }
        Ok(resp.json().await?)
    }

    pub async fn fetch_mint_info(&self) -> Result<MintInfo> {
        self.get_json("/v1/info").await
    }

    pub async fn fetch_keysets(&self) -> Result<KeysetsResponse> {
        self.get_json("/v1/keysets").await
    }

    pub async fn fetch_keys(&self, keyset_id: &str) -> Result<KeysResponse> {
        // NUT-01 spec: keys moved from /v1/keysets/{id} to /v1/keys/{id}
        // Try /v1/keys/{id} first (current spec), fall back to /v1/keysets/{id} (old spec)
        match self.get_json(&format!("/v1/keys/{}", keyset_id)).await {
            Ok(resp) => Ok(resp),
            Err(_) => self.get_json(&format!("/v1/keysets/{}", keyset_id)).await,
        }
    }

    pub async fn create_mint_quote(&self, amount: u64, unit: &str) -> Result<serde_json::Value> {
        let url = format!("{}/v1/mint/quote/bolt11", self.mint_url);
        let body = serde_json::json!({ "amount": amount, "unit": unit });
        let resp = self.client.post(&url).json(&body).send().await?;
        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            anyhow::bail!("POST /v1/mint/quote/bolt11 returned {}: {}", status, text);
        }
        Ok(resp.json().await?)
    }

    pub async fn check_mint_quote_state(&self, quote_id: &str) -> Result<serde_json::Value> {
        let url = format!("{}/v1/mint/quote/bolt11/{}", self.mint_url, quote_id);
        let resp = self.client.get(&url).send().await?;
        if !resp.status().is_success() {
            anyhow::bail!("GET quote state returned {}", resp.status());
        }
        Ok(resp.json().await?)
    }

    pub async fn check_proof_state(&self, ys: &[String]) -> Result<serde_json::Value> {
        let url = format!("{}/v1/checkstate", self.mint_url);
        let body = serde_json::json!({ "Ys": ys });
        let resp = self.client.post(&url).json(&body).send().await?;
        if !resp.status().is_success() {
            anyhow::bail!("POST /v1/checkstate returned {}", resp.status());
        }
        Ok(resp.json().await?)
    }

    pub async fn swap(&self, inputs: serde_json::Value, outputs: serde_json::Value) -> Result<serde_json::Value> {
        let url = format!("{}/v1/swap", self.mint_url);
        let body = serde_json::json!({ "inputs": inputs, "outputs": outputs });
        let resp = self.client.post(&url).json(&body).send().await?;
        let status = resp.status();
        let text = resp.text().await?;
        if !status.is_success() {
            anyhow::bail!("POST /v1/swap returned {}: {}", status, text);
        }
        Ok(serde_json::from_str(&text)?)
    }

    async fn post_raw(&self, path: &str, body: &serde_json::Value) -> Result<reqwest::Response> {
        let url = format!("{}{}", self.mint_url, path);
        let resp = self.client.post(&url).json(body).send().await?;
        Ok(resp)
    }

    pub async fn create_melt_quote_raw(&self, request: &str, unit: &str) -> Result<reqwest::Response> {
        let body = serde_json::json!({ "request": request, "unit": unit });
        self.post_raw("/v1/melt/quote/bolt11", &body).await
    }

    pub async fn restore_raw(&self, outputs: serde_json::Value) -> Result<reqwest::Response> {
        let body = serde_json::json!({ "outputs": outputs });
        self.post_raw("/v1/restore", &body).await
    }

    pub async fn subscribe_check(&self) -> Result<reqwest::Response> {
        let url = format!("{}/v1/ws", self.mint_url);
        let resp = self.client.get(&url)
            .header("Connection", "Upgrade")
            .header("Upgrade", "websocket")
            .header("Sec-WebSocket-Version", "13")
            .header("Sec-WebSocket-Key", "dGhlIHNhbXBsZSBub25jZQ==")
            .send().await?;
        Ok(resp)
    }
}

impl MintAuditor {
    pub async fn fetch_text(&self, path: &str) -> Result<reqwest::Response> {
        self.get(path).await
    }
}
