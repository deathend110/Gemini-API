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

$env:GEMINI_GATEWAY_API_KEY = $ApiKey
$env:GEMINI_GATEWAY_COOKIES_JSON_PATH = $CookiesJsonPath
$env:GEMINI_GATEWAY_PROXY = $Proxy
$env:GEMINI_GATEWAY_HOST = $GatewayHost
$env:GEMINI_GATEWAY_PORT = $Port.ToString()
$env:GEMINI_GATEWAY_DEFAULT_MODEL = $DefaultModel
$env:GEMINI_GATEWAY_DEFAULT_REASONING_EFFORT = $DefaultReasoningEffort
$env:GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ENABLED = $BrowserCookieRefreshEnabled.ToString().ToLower()
$env:GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ON_AUTH_ERROR = $BrowserCookieRefreshOnAuthError.ToString().ToLower()
$env:GEMINI_GATEWAY_BROWSER_PROFILE_DIR = $BrowserProfileDir
$env:GEMINI_GATEWAY_BROWSER_LOGIN_WAIT_SECONDS = $BrowserLoginWaitSeconds.ToString()
$env:GEMINI_GATEWAY_BROWSER_POLL_INTERVAL_SECONDS = $BrowserPollIntervalSeconds.ToString()
$env:GEMINI_GATEWAY_BROWSER_PAGE_LOAD_TIMEOUT_SECONDS = $BrowserPageLoadTimeoutSeconds.ToString()
$env:GEMINI_GATEWAY_BROWSER_HEADLESS = $BrowserHeadless.ToString().ToLower()

Write-Host "Gemini Gateway environment configured for current PowerShell session:"
Write-Host "  GEMINI_GATEWAY_API_KEY=$env:GEMINI_GATEWAY_API_KEY"
Write-Host "  GEMINI_GATEWAY_COOKIES_JSON_PATH=$env:GEMINI_GATEWAY_COOKIES_JSON_PATH"
Write-Host "  GEMINI_GATEWAY_PROXY=$env:GEMINI_GATEWAY_PROXY"
Write-Host "  GEMINI_GATEWAY_HOST=$env:GEMINI_GATEWAY_HOST"
Write-Host "  GEMINI_GATEWAY_PORT=$env:GEMINI_GATEWAY_PORT"
Write-Host "  GEMINI_GATEWAY_DEFAULT_MODEL=$env:GEMINI_GATEWAY_DEFAULT_MODEL"
Write-Host "  GEMINI_GATEWAY_DEFAULT_REASONING_EFFORT=$env:GEMINI_GATEWAY_DEFAULT_REASONING_EFFORT"
Write-Host "  GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ENABLED=$env:GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ENABLED"
Write-Host "  GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ON_AUTH_ERROR=$env:GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ON_AUTH_ERROR"
Write-Host "  GEMINI_GATEWAY_BROWSER_PROFILE_DIR=$env:GEMINI_GATEWAY_BROWSER_PROFILE_DIR"
Write-Host "  GEMINI_GATEWAY_BROWSER_LOGIN_WAIT_SECONDS=$env:GEMINI_GATEWAY_BROWSER_LOGIN_WAIT_SECONDS"
Write-Host "  GEMINI_GATEWAY_BROWSER_POLL_INTERVAL_SECONDS=$env:GEMINI_GATEWAY_BROWSER_POLL_INTERVAL_SECONDS"
Write-Host "  GEMINI_GATEWAY_BROWSER_PAGE_LOAD_TIMEOUT_SECONDS=$env:GEMINI_GATEWAY_BROWSER_PAGE_LOAD_TIMEOUT_SECONDS"
Write-Host "  GEMINI_GATEWAY_BROWSER_HEADLESS=$env:GEMINI_GATEWAY_BROWSER_HEADLESS"
Write-Host ""
Write-Host "Recommended next steps:"
Write-Host "  uv sync --extra browser"
Write-Host "  uv run --extra browser python -m gateway.refresh_cookies"
Write-Host "  如未登录 Gemini，请先运行 refresh_cookies 并复制其输出的 PowerShell 命令"
Write-Host "  登录完成后保持该专用 Chrome 继续运行，不要关闭窗口，再重新执行 refresh_cookies"
Write-Host "  uv run python -m gateway.main"
