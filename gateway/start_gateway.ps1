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

# If refresh_cookies reports that manual login is required, copy the printed
# PowerShell command. If the same profile was opened normally, close that
# Chrome window first, then relaunch with the printed command. After sign-in,
# keep that Chrome window running and rerun this script.
# 如提示缺少调试会话，请先关闭当前已普通打开的同 profile Chrome 窗口，再用打印出的命令重新启动。
# 登录完成后保持该专用 Chrome 继续运行，不要关闭窗口，再重新执行 refresh_cookies。

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
