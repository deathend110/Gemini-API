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

function Get-GatewayCookieValue {
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [object]$CookieData,

        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if ($null -eq $CookieData) {
        return $null
    }

    if ($CookieData -is [System.Collections.IDictionary]) {
        return $CookieData[$Name]
    }

    if (
        $CookieData -is [System.Collections.IEnumerable] -and
        -not ($CookieData -is [string])
    ) {
        foreach ($item in $CookieData) {
            if ($null -eq $item) {
                continue
            }

            $itemName = Get-GatewayCookieValue -CookieData $item -Name "name"
            if ($itemName -ne $Name) {
                continue
            }

            return Get-GatewayCookieValue -CookieData $item -Name "value"
        }
        return $null
    }

    $property = $CookieData.PSObject.Properties[$Name]
    if ($null -ne $property) {
        return $property.Value
    }

    return $null
}

function Test-GatewayCookiesJsonUsable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }

    try {
        $raw = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    }
    catch {
        return $false
    }

    $cookies = $raw
    if ($null -ne $raw) {
        $cookiesProperty = $raw.PSObject.Properties["cookies"]
        if ($null -ne $cookiesProperty) {
            $cookies = $cookiesProperty.Value
        }
    }

    $psid = Get-GatewayCookieValue -CookieData $cookies -Name "__Secure-1PSID"
    return ($psid -is [string]) -and (-not [string]::IsNullOrWhiteSpace($psid))
}

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

uv sync

if (Test-GatewayCookiesJsonUsable -Path $CookiesJsonPath) {
    Write-Host "Detected usable cookies.json at $CookiesJsonPath; 跳过 refresh_cookies，直接启动网关。"
}
else {
    Write-Host "缺少可用 cookies.json，先尝试从专用 Chrome profile 刷新登录态。"
    uv sync --extra browser
    uv run --extra browser python -m gateway.refresh_cookies
}

uv run python -m gateway.main
