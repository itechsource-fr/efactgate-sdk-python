# Efactgate SDK — API Universelle GW-eFactures (Python)

[![CI](https://github.com/itechsource-fr/efactgate-sdk-python/actions/workflows/ci.yml/badge.svg)](https://github.com/itechsource-fr/efactgate-sdk-python)
[![PyPI](https://img.shields.io/pypi/v/efactgate-sdk)](https://pypi.org/project/efactgate-sdk/)

Client Python asynchrone pour l'API Universelle GW-eFactures. Encapsule l'authentification,
la validation locale, l'envoi de factures, la récupération de statuts/ACK et la gestion
résiliente des erreurs réseau.

**Dépôt source :** [github.com/itechsource-fr/efactgate-sdk-python](https://github.com/itechsource-fr/efactgate-sdk-python)

## Installation

```bash
pip install efactgate-sdk
```

**Prérequis :** Python 3.11+

## Démarrage rapide

### Authentification par API Key

```python
import asyncio
from efactgate_sdk.client import EfactgateClient

async def main() -> None:
    async with EfactgateClient(
        base_url="https://api.gw-efactures.efactgate.io/api/v1",
        api_key="votre-clé-api",
    ) as client:
        # Le SDK inclut automatiquement le header X-API-Key
        status = await client.get_status("flux-id-existant")
        print(f"Statut : {status.status.value}")

asyncio.run(main())
```

### Authentification OAuth2 (client_credentials)

```python
import asyncio
from efactgate_sdk.client import EfactgateClient

async def main() -> None:
    async with EfactgateClient(
        base_url="https://api.gw-efactures.efactgate.io/api/v1",
        oauth_client_id="votre-client-id",
        oauth_client_secret="votre-client-secret",
        oauth_token_endpoint="https://auth.efactgate.io/oauth2/token",
    ) as client:
        # Le SDK obtient et rafraîchit le Bearer token automatiquement
        status = await client.get_status("flux-id-existant")
        print(f"Statut : {status.status.value}")

asyncio.run(main())
```

## Envoi d'une facture B2B

```python
import asyncio
from efactgate_sdk.client import EfactgateClient
from efactgate_sdk.models.invoice import InvoiceSubmission
from efactgate_sdk.models.enums import ImportFormat

async def envoyer_facture() -> None:
    async with EfactgateClient(
        base_url="https://api.gw-efactures.efactgate.io/api/v1",
        api_key="votre-clé-api",
    ) as client:
        facture = InvoiceSubmission(
            content='{"numero": "FA-2024-001", "montant_ttc": "1200.00"}',
            format="efactgate_json",
            target_connector_id="connector-chorus-pro",
            enterprise_siret="12345678901234",
            metadata={"departement": "comptabilite"},
        )

        # Validation locale + envoi
        resultat = await client.submit_invoice(facture)
        print(f"Flux créé : {resultat.flux_id}")
        print(f"Statut initial : {resultat.status.value}")

asyncio.run(envoyer_facture())
```

## Récupération de statut par flux_id

```python
import asyncio
from efactgate_sdk.client import EfactgateClient

async def consulter_statut() -> None:
    async with EfactgateClient(
        base_url="https://api.gw-efactures.efactgate.io/api/v1",
        api_key="votre-clé-api",
    ) as client:
        flux_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

        status = await client.get_status(flux_id)
        print(f"Statut courant : {status.status.value}")
        print(f"Type de flux : {status.flux_type.value}")

        # Historique des transitions
        for transition in status.transitions:
            print(
                f"  {transition.from_status.value} → {transition.to_status.value} "
                f"({transition.reason}) à {transition.transitioned_at.isoformat()}"
            )

asyncio.run(consulter_statut())
```

## Polling jusqu'à statut final

```python
import asyncio
from efactgate_sdk.client import EfactgateClient
from efactgate_sdk.exceptions import TimeoutError

async def attendre_statut_final() -> None:
    async with EfactgateClient(
        base_url="https://api.gw-efactures.efactgate.io/api/v1",
        api_key="votre-clé-api",
    ) as client:
        flux_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

        try:
            # Interroge toutes les 5s, timeout après 300s
            statut_final = await client.poll_until_final(
                flux_id,
                timeout=300.0,
                interval=5.0,
            )
            print(f"Statut terminal atteint : {statut_final.status.value}")

            # Récupérer l'ACK si accepté
            if statut_final.status.value == "accepte":
                ack = await client.get_ack(flux_id)
                if ack is not None:
                    print(f"ACK reçu à : {ack.received_at.isoformat()}")
        except TimeoutError as e:
            print(f"Timeout après {e.elapsed_seconds:.1f}s")
            print(f"Dernier statut observé : {e.last_status}")

asyncio.run(attendre_statut_final())
```

## Gestion des erreurs

```python
import asyncio
from efactgate_sdk.client import EfactgateClient
from efactgate_sdk.models.invoice import InvoiceSubmission
from efactgate_sdk.exceptions import (
    AuthenticationError,
    ConfigurationError,
    NotFoundError,
    EfactgateSDKError,
    TimeoutError,
    TransmissionError,
    ValidationError,
)

async def gestion_erreurs() -> None:
    try:
        async with EfactgateClient(
            base_url="https://api.gw-efactures.efactgate.io/api/v1",
            api_key="votre-clé-api",
        ) as client:
            facture = InvoiceSubmission(
                content='{"numero": "FA-2024-001"}',
                format="efactgate_json",
                target_connector_id="connector-chorus",
                enterprise_siret="12345678901234",
            )

            resultat = await client.submit_invoice(facture)
            print(f"Succès : {resultat.flux_id}")

    except ValidationError as e:
        # Erreur de validation locale (aucun appel réseau effectué)
        print(f"Validation échouée : {e.message}")
        for err in e.errors:
            print(f"  Champ {err.field} : [{err.code}] {err.message}")

    except AuthenticationError as e:
        # Credentials invalides ou rafraîchissement échoué
        print(f"Erreur d'authentification : {e.message}")

    except NotFoundError as e:
        # flux_id inexistant
        print(f"Flux introuvable : {e.flux_id}")

    except TransmissionError as e:
        # Toutes les tentatives épuisées (5xx, 429, erreur réseau)
        print(f"Transmission échouée après {e.attempts} tentatives")
        print(f"Code HTTP : {e.http_code}")

    except TimeoutError as e:
        # Polling expiré sans atteindre un statut terminal
        print(f"Timeout : {e.elapsed_seconds:.1f}s, dernier statut : {e.last_status}")

    except ConfigurationError as e:
        # Configuration invalide (URL, bornes, paramètres manquants)
        print(f"Erreur de configuration : {e.message}")

    except EfactgateSDKError as e:
        # Catch-all pour toute erreur SDK
        print(f"Erreur SDK : [{e.code}] {e.message}")

asyncio.run(gestion_erreurs())
```

## Options de configuration

### Paramètres d'initialisation

| Paramètre | Type | Défaut | Description |
|-----------|------|--------|-------------|
| `base_url` | `str` | — | URL de base de l'API (ou `EFACTGATE_API_URL`) |
| `api_key` | `str` | — | Clé API (ou `EFACTGATE_API_KEY`) |
| `oauth_client_id` | `str` | — | Client ID OAuth2 (ou `EFACTGATE_OAUTH_CLIENT_ID`) |
| `oauth_client_secret` | `str` | — | Client secret OAuth2 (ou `EFACTGATE_OAUTH_CLIENT_SECRET`) |
| `oauth_token_endpoint` | `str` | — | URL du token endpoint OAuth2 |
| `timeout` | `float` | `30.0` | Timeout par requête en secondes (bornes : 1–300) |
| `max_retries` | `int` | `5` | Nombre max de tentatives (bornes : 0–10) |
| `sandbox` | `bool` | `False` | Mode sandbox (force l'URL sandbox) |
| `log_level` | `str` | `"WARNING"` | Niveau de log (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `hooks` | `EventHooks` | `None` | Hooks d'observabilité personnalisés |
| `retry_delays` | `tuple[float, ...]` | `(1, 2, 4, 8, 16)` | Délais de retry en secondes |

### Variables d'environnement

Les paramètres explicites ont toujours priorité sur les variables d'environnement.

| Variable | Équivalent paramètre |
|----------|---------------------|
| `EFACTGATE_API_URL` | `base_url` |
| `EFACTGATE_API_KEY` | `api_key` |
| `EFACTGATE_OAUTH_CLIENT_ID` | `oauth_client_id` |
| `EFACTGATE_OAUTH_CLIENT_SECRET` | `oauth_client_secret` |

### Mode Sandbox

En mode sandbox, toutes les requêtes sont redirigées vers l'environnement de test.
Aucune requête ne peut atteindre la production.

```python
client = EfactgateClient(
    api_key="test-key",
    sandbox=True,  # URL forcée vers https://sandbox.gw-efactures.efactgate.io/api/v1
)
```

### Validation locale sans envoi

```python
from efactgate_sdk.client import EfactgateClient
from efactgate_sdk.models.invoice import InvoiceSubmission

client = EfactgateClient(base_url="https://api.efactgate.io/api/v1", api_key="key")

facture = InvoiceSubmission(
    content='{"numero": "FA-2024-001"}',
    format="efactgate_json",
    target_connector_id="connector-test",
    enterprise_siret="12345678901234",
)

erreurs = client.validate(facture)
if erreurs:
    for err in erreurs:
        print(f"  {err.field}: {err.message}")
else:
    print("Facture valide !")
```

## Méthodes disponibles

| Méthode | Description |
|---------|-------------|
| `submit_invoice(invoice)` | Envoi d'une facture B2B unitaire |
| `submit_ereporting(data)` | Envoi de données e-Reporting B2C |
| `submit_batch(documents)` | Envoi en lot (1 à 1000 documents) |
| `import_file(path, format)` | Import de fichier (CSV, UBL, CII, Factur-X) |
| `get_status(flux_id)` | Récupération du statut courant |
| `get_ack(flux_id)` | Récupération de l'accusé de réception |
| `poll_until_final(flux_id)` | Polling jusqu'à statut terminal |
| `validate(invoice)` | Validation locale sans appel réseau |
| `close()` | Fermeture du transport HTTP |

## Développement

```bash
pip install -e ".[dev]"
pytest
mypy efactgate_sdk --strict
ruff check efactgate_sdk
```
