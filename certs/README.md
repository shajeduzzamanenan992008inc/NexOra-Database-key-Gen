# Trusted CA certificates

## `prod-ca-2021.crt`

Supabase's private root CA. The Supabase pooler (`*.pooler.supabase.com`) serves
a chain signed by `Supabase Root 2021 CA`, which is not part of the public
system trust store. Without this bundle the application fails at boot with:

```
ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED]
certificate verify failed: self-signed certificate in certificate chain
```

Point the app at it with `DATABASE_SSLROOTCERT=certs/prod-ca-2021.crt`.

| Field | Value |
| --- | --- |
| Subject / Issuer | `CN=Supabase Root 2021 CA, O=Supabase Inc` |
| Self-signed | yes |
| SHA-256 fingerprint | `80:70:25:AD:50:D4:ED:21:9D:2C:9C:7D:29:9C:00:4F:82:4E:B0:0C:F7:F6:5A:FE:F6:07:D0:7B:72:E6:CA:FA` |
| Valid until | 2031-04-26 |

The official copy is downloadable from the Supabase dashboard under
**Database -> SSL Configuration** (`prod-ca-2021.crt`). The file here was
captured from the live TLS handshake of the project's pooler and matches the
published root (same subject, self-signed, same validity window); compare the
fingerprint before replacing it.

Rotating the CA means updating this file and restarting the app. Do not disable
verification (`sslmode=disable`) to work around a trust-store problem.
