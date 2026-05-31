param(
    [string]$ApiKey = "your-local-key",
    [string]$GatewayHost = "127.0.0.1",
    [int]$Port = 8010,
    [string]$DefaultModel = "gemini-3.5-flash",
    [ValidateSet("standard", "extended")]
    [string]$DefaultReasoningEffort = "standard",
    [string]$Proxy = "http://127.0.0.1:10090/",
    [string]$CookiesJsonPath = (Join-Path (Split-Path $PSScriptRoot -Parent) "cookies.json")
)

$env:GEMINI_GATEWAY_API_KEY = $ApiKey
$env:GEMINI_GATEWAY_COOKIES_JSON_PATH = $CookiesJsonPath
$env:GEMINI_GATEWAY_PROXY = $Proxy
$env:GEMINI_GATEWAY_HOST = $GatewayHost
$env:GEMINI_GATEWAY_PORT = $Port.ToString()
$env:GEMINI_GATEWAY_DEFAULT_MODEL = $DefaultModel
$env:GEMINI_GATEWAY_DEFAULT_REASONING_EFFORT = $DefaultReasoningEffort

Write-Host "Gemini Gateway environment configured for current PowerShell session:"
Write-Host "  GEMINI_GATEWAY_API_KEY=$env:GEMINI_GATEWAY_API_KEY"
Write-Host "  GEMINI_GATEWAY_COOKIES_JSON_PATH=$env:GEMINI_GATEWAY_COOKIES_JSON_PATH"
Write-Host "  GEMINI_GATEWAY_PROXY=$env:GEMINI_GATEWAY_PROXY"
Write-Host "  GEMINI_GATEWAY_HOST=$env:GEMINI_GATEWAY_HOST"
Write-Host "  GEMINI_GATEWAY_PORT=$env:GEMINI_GATEWAY_PORT"
Write-Host "  GEMINI_GATEWAY_DEFAULT_MODEL=$env:GEMINI_GATEWAY_DEFAULT_MODEL"
Write-Host "  GEMINI_GATEWAY_DEFAULT_REASONING_EFFORT=$env:GEMINI_GATEWAY_DEFAULT_REASONING_EFFORT"
Write-Host ""
Write-Host "Next step:"
Write-Host "  python -m gateway.main"
