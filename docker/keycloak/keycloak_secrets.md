# Documentation for Keycloak secret management

## 1. Keycloak Admin Password
- The administrator password has been removed from the `docker-compose.yml` file for security reasons. 
- It is stored locally in `./secrets/keycloak_admin_password.txt`, which ist excluded via '.gitignore'.
- Keycloak reads the passwort via the mapped secret file path.

## 2. Client Secrets
The client secrets for controller-client and lstm-client have been hardcoded as plain text in `iot-realm.json`. 
- This is not ideal for security, but implementing a dynamic entrypoint or a vault system to inject the secrets 
    at runtime would introduce architectural overhead and add complexity.