param(
    [string]$ApiKey = "gemini-api",
    [string]$GatewayHost = "127.0.0.1",
    [int]$Port = 8010,
    [string]$DefaultModel = "gemini-3-flash",
    [ValidateSet("standard", "extended")]
    [string]$DefaultReasoningEffort = "standard",
    [string]$Proxy = "http://127.0.0.1:10090/",
    [string]$CookiesJsonPath = (Join-Path (Split-Path $PSScriptRoot -Parent) "cookies.json"),
    [bool]$BrowserCookieRefreshEnabled = $false,
    [bool]$BrowserCookieRefreshOnAuthError = $false,
    [string]$BrowserProfileDir = (Join-Path ([Environment]::GetFolderPath("UserProfile")) ".gemini-api\selenium-profile"),
    [int]$BrowserLoginWaitSeconds = 300,
    [int]$BrowserPollIntervalSeconds = 2,
    [int]$BrowserPageLoadTimeoutSeconds = 60,
    [bool]$BrowserHeadless = $false
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "set_gateway_env.ps1") `
  -ApiKey $ApiKey `
  -GatewayHost $GatewayHost `
  -Port $Port `
  -DefaultModel $DefaultModel `
  -DefaultReasoningEffort $DefaultReasoningEffort `
  -Proxy $Proxy `
  -CookiesJsonPath $CookiesJsonPath `
  -BrowserCookieRefreshEnabled $BrowserCookieRefreshEnabled `
  -BrowserCookieRefreshOnAuthError $BrowserCookieRefreshOnAuthError `
  -BrowserProfileDir $BrowserProfileDir `
  -BrowserLoginWaitSeconds $BrowserLoginWaitSeconds `
  -BrowserPollIntervalSeconds $BrowserPollIntervalSeconds `
  -BrowserPageLoadTimeoutSeconds $BrowserPageLoadTimeoutSeconds `
  -BrowserHeadless $BrowserHeadless

uv sync --extra browser
uv run --extra browser python -m gateway.refresh_cookies
uv run python -m gateway.main
