# Security Policy

## Supported Versions

Only the current state of `main` is supported with security fixes — there are no maintained release branches. Please make sure you're working against the latest commit before reporting an issue.

## Reporting a Vulnerability

If you discover a security vulnerability in this project (the backend pipeline, the services layer, or any of the three frontend apps), please report it privately using GitHub's [private vulnerability reporting](../../security/advisories/new) feature rather than opening a public issue.

You can expect an initial response within a few days. If the vulnerability is confirmed, a fix will be released and credited to you (unless you prefer to remain anonymous); if it's declined, you'll get an explanation why.

## Scope notes

This is a data-visualization project, not a service that handles user accounts, payments, or private user data — the frontend apps are read-only viewers over public satellite imagery, and the backend/services layer runs on infrastructure the maintainer controls. Reports about the acquisition/processing pipeline, the services configuration (THREDDS/xcube/FastAPI/nginx), or the frontend apps are all in scope; reports about third-party dependencies (satpy, pyresample, CesiumJS, OpenLayers, etc.) should generally go to those projects directly, but feel free to report here too if you're not sure which project is responsible.
