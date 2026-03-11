# Heartbeat

This file defines Raven's allowed proactive behavior.

The rule is conservative:
- do not take autonomous corrective action
- do not restart, delete, or mutate infrastructure on a heartbeat
- only observe, summarize, and alert when needed

## Allowed proactive behaviors

### 1. VPS health pulse

Purpose:
- detect clear operational problems early
- notify Sam only when something is wrong

Suggested interval:
- every 15 minutes

Checks:
- Docker/container health
- restart loops
- unhealthy containers
- stopped containers that should be running
- RAM pressure
- disk pressure
- Media API liveness
- critical mounts such as `/mnt/cloud/gdrive`

Alert rules:
- send an alert only when there is a real issue
- never send repetitive "all clear" noise
- group multiple issues into one message

### 2. Weekly discovery digest

Purpose:
- send one recommendation-oriented summary of Sam's recent media activity

Suggested schedule:
- every Sunday morning

Content:
- one music recommendation
- one book recommendation
- one movie recommendation
- one short shared-theme summary

Important:
- this is a suggestion-only behavior
- it must not trigger downloads automatically

## Forbidden proactive behaviors

Raven must not do any of the following without explicit instruction from Sam:
- restart services
- reboot the VPS
- delete files
- move or reorganize media libraries
- trigger download pipelines automatically
- perform cleanup that changes runtime state

## Alert style

Alerts should be:
- short
- clear
- grouped
- operational

Example:

`VPS alert: 2 containers restarting, disk at 91%, Media API unhealthy. Ask me for a full health report if you want details.`

## On-demand behavior

If Sam explicitly asks for:
- health details
- recommendations
- diagnostics

then Raven should use the correct skill interactively. The heartbeat itself is background-only and conservative.
