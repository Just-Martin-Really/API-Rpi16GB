# Keycloak secret management

## 1. Keycloak admin password

- The administrator password has been removed from `docker-compose.yml`.
- It lives in `./secrets/keycloak_admin_password.txt`, excluded via `.gitignore`.
- Keycloak reads it via the mapped file path `KEYCLOAK_ADMIN_PASSWORD_FILE=/run/secrets/keycloak_admin_password`.
- Setup instructions: `docs/backend/setup.md` § 1.8.

## 2. Client secrets

The client secrets for `controller-client` and `lstm-client` are hardcoded as plain text in `iot-realm.json`. A dynamic entrypoint or vault-based injection would add architectural complexity that this lab setup does not warrant.

| Client | `secret` in `iot-realm.json` | Secret file consumed by the service |
|---|---|---|
| `controller-client` | `sc_controller_client` | `docker/secrets/keycloak_controller_secret.txt` |
| `lstm-client` | `sc_lstm_client` | `docker/secrets/keycloak_lstm_secret.txt` |

The contents of the two `.txt` files **must be identical** to the corresponding `secret` field in `iot-realm.json`. The `controller` and `lstm` services mount these files at `/run/secrets/keycloak_controller_secret` / `/run/secrets/keycloak_lstm_secret`, read them at startup, and send the value as `client_secret` in the OAuth2 client-credentials request.

## 3. Rotating a client secret

1. Pick a new value, e.g. `openssl rand -base64 24`.
2. Update the `secret` field in `docker/keycloak/iot-realm.json`.
3. Overwrite `docker/secrets/keycloak_<client>_secret.txt` with the same value.
4. Restart Keycloak so the realm import re-applies, or push the new secret via the Keycloak Admin API and only update the `.txt` file.

## 4. Production checklist

Before this stack leaves the lab, the hardcoded values **must** be replaced with rotated random strings and the `.gitignore` for `docker/secrets/` must be verified. See the production checklist at the bottom of `docs/backend/keycloak-integration.md`.