mod types;
mod audits;

use clap::{Parser, ValueEnum};
use types::*;
use anyhow::Result;
use std::time::Instant;

#[derive(Parser)]
#[command(name = "nut-auditor")]
#[command(about = "Cashu NUT specification conformance auditor")]
struct Cli {
    /// Mint URL(s) to audit. Can specify multiple for comparison mode.
    #[arg(required = true)]
    mints: Vec<String>,

    /// Output format
    #[arg(long, value_enum, default_value_t = OutputFormat::Text)]
    format: OutputFormat,

    /// Which NUTs to test (default: all)
    #[arg(long, value_delimiter = ',')]
    nuts: Option<Vec<String>>,

    /// Output file (default: stdout)
    #[arg(long)]
    output: Option<String>,
}

#[derive(Clone, ValueEnum)]
enum OutputFormat {
    Text,
    Json,
    Markdown,
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    let compare_mode = cli.mints.len() > 1;

    let mut reports = Vec::new();

    for mint_url in &cli.mints {
        eprintln!("Auditing {} ...", mint_url);
        let report = audit_mint(mint_url, &cli).await;
        reports.push(report);
    }

    match cli.format {
        OutputFormat::Json => {
            let json = serde_json::to_string_pretty(&reports)?;
            output(&cli, &json)?;
        }
        OutputFormat::Text => {
            for report in &reports {
                output(&cli, &format_report_text(report))?;
            }
            if compare_mode {
                output(&cli, &format_comparison_table(&reports))?;
            }
        }
        OutputFormat::Markdown => {
            for report in &reports {
                output(&cli, &format_report_markdown(report))?;
            }
            if compare_mode {
                output(&cli, &format_comparison_markdown(&reports))?;
            }
        }
    }

    Ok(())
}

async fn audit_mint(mint_url: &str, cli: &Cli) -> AuditReport {
    let auditor = MintAuditor::new(mint_url);
    let mut results = Vec::new();
    let mut mint_info = None;
    let mut claimed_nuts = Vec::new();

    let should_test = |nut: &str| -> bool {
        match &cli.nuts {
            None => true,
            Some(nuts) => nuts.iter().any(|n| n.eq_ignore_ascii_case(nut)),
        }
    };

    // NUT-06: Always run first — need info to know what to test
    if should_test("06") || should_test("NUT-06") || cli.nuts.is_none() {
        let nut06_results = audits::audit_nut06(&auditor).await;
        if let Some(r) = nut06_results.iter().find(|r| r.name.contains("NUT claims")) {
            if r.status == TestStatus::Pass {
                // Re-fetch to get claimed NUTs
                if let Ok(info) = auditor.fetch_mint_info().await {
                    mint_info = Some(info.clone());
                    if let Some(nuts) = &info.nuts {
                        claimed_nuts = nuts.keys().cloned().collect();
                    }
                }
            }
        }
        results.extend(nut06_results);
    }

    // NUT-01: Keysets and keys
    if should_test("01") || should_test("NUT-01") || cli.nuts.is_none() {
        results.extend(audits::audit_nut01(&auditor).await);
    }

    // NUT-02: Keyset ID version validation
    if should_test("02") || should_test("NUT-02") || cli.nuts.is_none() {
        results.extend(audits::audit_nut02(&auditor).await);
    }

    // NUT-04: Mint quote flow
    if should_test("04") || should_test("NUT-04") || cli.nuts.is_none() {
        if claimed_nuts.contains(&"4".to_string()) || claimed_nuts.is_empty() {
            results.extend(audits::audit_nut04(&auditor).await);
        } else {
            results.push(skip_result("NUT-04", "mint does not claim NUT-04"));
        }
    }

    // NUT-05: Melt quote flow
    if should_test("05") || should_test("NUT-05") || cli.nuts.is_none() {
        if claimed_nuts.contains(&"5".to_string()) || claimed_nuts.is_empty() {
            results.extend(audits::audit_nut05(&auditor).await);
        } else {
            results.push(skip_result("NUT-05", "mint does not claim NUT-05"));
        }
    }

    // NUT-07: Checkstate
    if should_test("07") || should_test("NUT-07") || cli.nuts.is_none() {
        if claimed_nuts.contains(&"7".to_string()) || claimed_nuts.is_empty() {
            results.extend(audits::audit_nut07(&auditor).await);
        } else {
            results.push(skip_result("NUT-07", "mint does not claim NUT-07"));
        }
    }

    // NUT-08: Fee handling
    if should_test("08") || should_test("NUT-08") || cli.nuts.is_none() {
        if claimed_nuts.contains(&"8".to_string()) || claimed_nuts.is_empty() {
            results.extend(audits::audit_nut08(&auditor).await);
        } else {
            results.push(skip_result("NUT-08", "mint does not claim NUT-08"));
        }
    }

    // NUT-09: Restore endpoint
    if should_test("09") || should_test("NUT-09") || cli.nuts.is_none() {
        if claimed_nuts.contains(&"9".to_string()) || claimed_nuts.is_empty() {
            results.extend(audits::audit_nut09(&auditor).await);
        } else {
            results.push(skip_result("NUT-09", "mint does not claim NUT-09"));
        }
    }

    // NUT-17: WebSocket subscription
    if should_test("17") || should_test("NUT-17") || cli.nuts.is_none() {
        results.extend(audits::audit_nut17(&auditor, mint_info.as_ref()).await);
    }

    // NUT-19: Cache headers
    if should_test("19") || should_test("NUT-19") || cli.nuts.is_none() {
        if claimed_nuts.contains(&"19".to_string()) || claimed_nuts.is_empty() {
            results.extend(audits::audit_nut19(&auditor, mint_info.as_ref()).await);
        } else {
            results.push(skip_result("NUT-19", "mint does not claim NUT-19"));
        }
    }

    // NUT-20: Quote signature pubkey
    if should_test("20") || should_test("NUT-20") || cli.nuts.is_none() {
        results.extend(audits::audit_nut20(&auditor, mint_info.as_ref()).await);
    }

    // NUT-29: Batch mint support
    if should_test("29") || should_test("NUT-29") || cli.nuts.is_none() {
        results.extend(audits::audit_nut29(&auditor, mint_info.as_ref()).await);
    }

    let summary = AuditSummary::from_results(&results);

    AuditReport {
        mint_url: mint_url.to_string(),
        timestamp: chrono_now(),
        mint_info,
        claimed_nuts,
        results,
        summary,
    }
}

fn skip_result(nut: &str, reason: &str) -> NutTestResult {
    NutTestResult {
        nut: nut.into(),
        name: format!("{} — skipped", nut),
        status: TestStatus::Skip,
        detail: reason.into(),
        duration_ms: 0,
    }
}

fn chrono_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
    format!("{}", secs)
}

fn format_report_text(report: &AuditReport) -> String {
    let mut out = String::new();
    out.push_str(&format!("\n=== NUT Audit: {} ===\n", report.mint_url));
    out.push_str(&format!("Timestamp: {}\n", report.timestamp));

    if let Some(info) = &report.mint_info {
        out.push_str(&format!("Mint: {:?} v{:?}\n", info.name, info.version));
        out.push_str(&format!("Claims: {} NUTs ({})\n", report.claimed_nuts.len(), report.claimed_nuts.join(", ")));
    }

    out.push_str(&format!("\n{:<8} {:<50} {:<6} {}\n", "NUT", "Test", "Status", "Detail"));
    let dash_line = "-".repeat(120); out.push_str(&dash_line); out.push('\n');

    for r in &report.results {
        let status_str = match r.status {
            TestStatus::Pass => "✅",
            TestStatus::Fail => "❌",
            TestStatus::Skip => "⏭️",
            TestStatus::Warn => "⚠️",
        };
        out.push_str(&format!("{:<8} {:<50} {:<6} {}\n",
            r.nut, {&r.name.as_str()[..r.name.len().min(50)]}, status_str, r.detail));
    }

    out.push_str(&format!("\nSummary: {} total | {} pass | {} fail | {} skip | {} warn\n",
        report.summary.total, report.summary.passed, report.summary.failed,
        report.summary.skipped, report.summary.warnings));

    out
}

fn format_comparison_table(reports: &[AuditReport]) -> String {
    let mut out = String::from("\n=== Comparison ===\n\n");

    // Collect all NUT numbers across all mints
    let mut all_nuts = std::collections::BTreeSet::new();
    for r in reports {
        for result in &r.results {
            all_nuts.insert(result.nut.clone());
        }
    }

    // Header
    out.push_str(&format!("{:<8}", "NUT"));
    for r in reports {
        let name = r.mint_url.replace("https://", "");
        out.push_str(&format!(" | {:<30}", name.as_str()));
    }
    out.push_str("\n");
    let dash_line = "-".repeat(8 + reports.len() * 33); out.push_str(&dash_line);
    out.push_str("\n");

    // Rows
    for nut in &all_nuts {
        out.push_str(&format!("{:<8}", nut));
        for r in reports {
            let nut_results: Vec<_> = r.results.iter().filter(|x| &x.nut == nut).collect();
            if nut_results.is_empty() {
                out.push_str(" | ---");
            } else {
                let any_fail = nut_results.iter().any(|x| x.status == TestStatus::Fail);
                let any_warn = nut_results.iter().any(|x| x.status == TestStatus::Warn);
                let all_pass = nut_results.iter().all(|x| x.status == TestStatus::Pass);
                let all_skip = nut_results.iter().all(|x| x.status == TestStatus::Skip);
                let status = if all_pass { "✅" }
                    else if all_skip { "⏭️" }
                    else if any_fail { "❌" }
                    else if any_warn { "⚠️" }
                    else { "?" };
                out.push_str(&format!(" | {:<30}", status));
            }
        }
        out.push_str("\n");
    }

    out
}

fn format_report_markdown(report: &AuditReport) -> String {
    let mut out = format!("## NUT Audit: `{}`\n\n", report.mint_url);
    out.push_str(&format!("**Timestamp**: {}\n\n", report.timestamp));

    if let Some(info) = &report.mint_info {
        out.push_str(&format!("**Mint**: {} v{}\n\n", info.name.as_deref().unwrap_or("?"), info.version.as_deref().unwrap_or("?")));
        out.push_str(&format!("**Claims**: {} NUTs ({})\n\n", report.claimed_nuts.len(), report.claimed_nuts.join(", ")));
    }

    out.push_str("| NUT | Test | Status | Detail |\n");
    out.push_str("|-----|------|--------|--------|\n");

    for r in &report.results {
        let status_str = match r.status {
            TestStatus::Pass => "✅ Pass",
            TestStatus::Fail => "❌ Fail",
            TestStatus::Skip => "⏭️ Skip",
            TestStatus::Warn => "⚠️ Warn",
        };
        out.push_str(&format!("| {} | {} | {} | {} |\n", r.nut, r.name, status_str, r.detail.replace('|', "\\|")));
    }

    out.push_str(&format!("\n**Summary**: {} total, {} pass, {} fail, {} skip, {} warn\n",
        report.summary.total, report.summary.passed, report.summary.failed,
        report.summary.skipped, report.summary.warnings));

    out
}

fn format_comparison_markdown(reports: &[AuditReport]) -> String {
    let mut out = String::from("## Comparison\n\n");

    let mut all_nuts = std::collections::BTreeSet::new();
    for r in reports {
        for result in &r.results {
            all_nuts.insert(result.nut.clone());
        }
    }

    // Header
    out.push_str("| NUT |");
    for r in reports {
        let name = r.mint_url.replace("https://", "");
        out.push_str(&format!(" {} |", name.as_str()));
    }
    out.push_str("\n|");

    for _ in reports {
        out.push_str("---|");
    }
    out.push_str("\n");

    for nut in &all_nuts {
        out.push_str(&format!("| {} |", nut));
        for r in reports {
            let nut_results: Vec<_> = r.results.iter().filter(|x| &x.nut == nut).collect();
            if nut_results.is_empty() {
                out.push_str(" --- |");
            } else {
                let any_fail = nut_results.iter().any(|x| x.status == TestStatus::Fail);
                let any_warn = nut_results.iter().any(|x| x.status == TestStatus::Warn);
                let all_pass = nut_results.iter().all(|x| x.status == TestStatus::Pass);
                let all_skip = nut_results.iter().all(|x| x.status == TestStatus::Skip);
                let status = if all_pass { "✅" }
                    else if all_skip { "⏭️" }
                    else if any_fail { "❌" }
                    else if any_warn { "⚠️" }
                    else { "❓" };
                out.push_str(&format!(" {} |", status));
            }
        }
        out.push_str("\n");
    }

    out
}

fn output(cli: &Cli, text: &str) -> Result<()> {
    match &cli.output {
        Some(path) => std::fs::write(path, text)?,
        None => println!("{}", text),
    }
    Ok(())
}
