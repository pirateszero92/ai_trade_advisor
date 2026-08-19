Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  AI Trade Advisor - Nginx Web Build & Run" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

$rootDir = Split-Path -Parent $PSScriptRoot
$mobileDir = Join-Path $rootDir "mobile"

# 1. Build Flutter Web
Write-Host "[1/3] Building Flutter Web Application..." -ForegroundColor Yellow
Set-Location $mobileDir
flutter build web

if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Flutter Web build failed!" -ForegroundColor Red
    exit 1
}

# 2. Check Docker
Write-Host "[2/3] Checking Docker daemon..." -ForegroundColor Yellow
Set-Location $rootDir
$dockerAvailable = Get-Command docker -ErrorAction SilentlyContinue

if ($dockerAvailable) {
    Write-Host "[3/3] Building and starting Nginx & Backend containers..." -ForegroundColor Green
    docker compose up --build -d
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host " Web App running at: http://localhost" -ForegroundColor Green
    Write-Host " Web App (Alt Port): http://localhost:3000" -ForegroundColor Green
    Write-Host " Backend API docs:   http://localhost:8000/docs" -ForegroundColor Green
    Write-Host "==========================================" -ForegroundColor Cyan
} else {
    Write-Host "[!] Docker not detected in PATH. Built static files are ready at:" -ForegroundColor Yellow
    Write-Host "    $rootDir\mobile\build\web" -ForegroundColor White
    Write-Host "    Copy these to your Nginx root directory (e.g. /usr/share/nginx/html) or use nginx.conf provided in ./nginx/" -ForegroundColor White
}
