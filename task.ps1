<#
.SYNOPSIS
  Equivalente ao Makefile para Windows, onde `make` não existe.

.EXAMPLE
  .\task.ps1 up
  .\task.ps1 migrate
  .\task.ps1 revision -m "cria tabela users"
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Task = 'help',

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Invoke-Compose { docker compose @args; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
function Invoke-Api { docker compose exec -T api @args; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
function Invoke-Web { docker compose exec -T web @args; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }

function Initialize-Env {
    if (-not (Test-Path '.env')) {
        Copy-Item '.env.example' '.env'
        Write-Host '  .env criado a partir de .env.example' -ForegroundColor Green
    }
}

function Get-HostPort {
    param([string]$Key, [string]$Default)
    if (Test-Path '.env') {
        $line = Select-String -Path '.env' -Pattern "^\s*$Key\s*=\s*(.+)$" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($line) { return $line.Matches[0].Groups[1].Value.Trim() }
    }
    return $Default
}

switch ($Task.ToLowerInvariant()) {
    'help' {
        Write-Host ''
        Write-Host '  Uso: .\task.ps1 <alvo>' -ForegroundColor Cyan
        Write-Host ''
        @(
            @('env',       'Cria .env a partir do .env.example')
            @('build',     'Constrói as imagens')
            @('up',        'Sobe a stack completa')
            @('down',      'Derruba a stack (mantém os volumes)')
            @('nuke',      'Derruba a stack e APAGA os volumes')
            @('logs',      'Segue os logs')
            @('ps',        'Estado dos serviços')
            @('shell',     'Shell no container da API')
            @('psql',      'psql no banco de desenvolvimento')
            @('migrate',   'Aplica as migrations pendentes')
            @('downgrade', 'Reverte a última migration')
            @('revision',  'Gera migration: .\task.ps1 revision -m "mensagem"')
            @('seed',      'Popula dados base (idempotente)')
            @('test',      'pytest + vitest')
            @('test-api',  'Só pytest')
            @('test-web',  'Só vitest')
            @('lint',      'ruff + mypy + eslint')
            @('fmt',       'Formata o código')
        ) | ForEach-Object { Write-Host ("    {0,-12} {1}" -f $_[0], $_[1]) }
        Write-Host ''
    }

    'env' { Initialize-Env }

    'build' { Initialize-Env; Invoke-Compose build }

    'up' {
        Initialize-Env
        Invoke-Compose up -d --build
        $port = Get-HostPort 'NGINX_HOST_PORT' '8080'
        Write-Host ''
        Write-Host "  web    http://localhost:$port"       -ForegroundColor Green
        Write-Host "  api    http://localhost:$port/api"   -ForegroundColor Green
        Write-Host "  docs   http://localhost:$port/api/docs" -ForegroundColor Green
    }

    'down' { Invoke-Compose down }
    'nuke' { Invoke-Compose down -v }
    'logs' { docker compose logs -f --tail=100 }
    'ps'   { Invoke-Compose ps }

    'shell' { docker compose exec api bash }

    'psql' {
        $user = Get-HostPort 'POSTGRES_USER' 'bolao'
        $db   = Get-HostPort 'POSTGRES_DB'   'bolao'
        docker compose exec postgres psql -U $user -d $db
    }

    'migrate'   { Invoke-Api alembic upgrade head }
    'downgrade' { Invoke-Api alembic downgrade -1 }

    'revision' {
        $message = $null
        for ($i = 0; $i -lt $Rest.Count; $i++) {
            if ($Rest[$i] -in @('-m', '--message')) { $message = $Rest[$i + 1]; break }
        }
        if (-not $message) { $message = ($Rest -join ' ').Trim() }
        if (-not $message) { throw 'Informe a mensagem: .\task.ps1 revision -m "mensagem"' }
        Invoke-Api alembic revision --autogenerate -m $message
    }

    'seed' { Invoke-Api python -m app.cli seed }

    'test'     { Invoke-Api pytest; Invoke-Web npm run test }
    'test-api' { Invoke-Api pytest }
    'test-web' { Invoke-Web npm run test }

    'lint' {
        Invoke-Api ruff check app tests
        Invoke-Api ruff format --check app tests
        Invoke-Api mypy app/scoring app/services
        Invoke-Web npm run lint
    }

    'fmt' {
        Invoke-Api ruff check --fix app tests
        Invoke-Api ruff format app tests
    }

    default {
        Write-Host "Alvo desconhecido: $Task" -ForegroundColor Red
        Write-Host "Rode  .\task.ps1 help  para ver a lista." -ForegroundColor Yellow
        exit 1
    }
}
