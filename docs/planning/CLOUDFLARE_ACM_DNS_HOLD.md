# Cloudflare ACM DNS hold

`logan911.com` is registered at Hostinger, but authoritative DNS is delegated to
**Cloudflare**. Every record below is created in Cloudflare. Do not change
nameservers and do not add or edit DNS records in Hostinger — records added there
have no effect, because that zone is not authoritative.

The certificate stack now requests **two** certificates, and each needs its own
validation CNAME. Publishing only one leaves the other stuck at
`PENDING_VALIDATION` indefinitely, with no error anywhere to explain it.

| Certificate | Hostname | Consumed by | Hold status |
|---|---|---|---|
| `PilotCertificate` | `aws.logan911.com` | ALB HTTPS listener | **Satisfied** — see below |
| `AuthCertificate` | `auth.logan911.com` | Cognito custom domain (managed login) | **Open** — see below |

---

## Hold 1 — `aws.logan911.com` (satisfied)

Kept for the record. This validation CNAME is already published and the
certificate is `ISSUED`, which is what lets the ALB serve HTTPS today.

| Setting | Exact value |
|---|---|
| Type | `CNAME` |
| Name | `_313fa8e4125f9012d0e6bdfe254706cb.aws.logan911.com` |
| Target | `_ed12f3d542d86c927869d4ee9b325bfd.jkddzztszm.acm-validations.aws` |
| Proxy status | `DNS only` (gray cloud) |
| TTL | `Auto` is acceptable |

Certificate ARN:
`arn:aws:acm:us-east-1:862772137583:certificate/847de721-b0f5-4c3d-8ec1-c27dd51a201d`

Do not delete this record. Removing it can cause ACM to fail renewal, which
would eventually break HTTPS on the application hostname.

---

## Hold 2 — `auth.logan911.com` (open)

This certificate exists in code but its validation CNAME **cannot be written
down in advance**: ACM generates the name and value when the certificate is
requested. So the order is:

1. Deploy the certificate stack. It will now create the second certificate
   alongside the existing one. The existing certificate is untouched — a separate
   resource, not a SAN edit, specifically so this cannot disturb the live
   listener.
2. Read the new validation record. Either open the certificate in the ACM console
   and copy the CNAME name/value, or:

   ```powershell
   & 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' acm describe-certificate `
     --certificate-arn <AuthCertificateArn from the stack output> `
     --region us-east-1 `
     --profile lcdash-sandbox-admin `
     --query 'Certificate.DomainValidationOptions[0].ResourceRecord' `
     --output json
   ```

3. Create it in Cloudflare exactly as issued, with **`Proxy status: DNS only`**.
   A proxied validation record does not validate.

   > Cloudflare appends the zone name to whatever goes in the **Name** field,
   > while ACM shows the fully-qualified name. Enter only the part **before**
   > `.logan911.com`. Pasting the whole value produces
   > `…_domain_validation.auth.logan911.com.logan911.com` and validation never
   > completes. After saving, confirm the record list shows `.logan911.com`
   > exactly once.

4. Verify from public DNS before waiting on ACM. This separates "the record is
   wrong" from "ACM has not polled yet" — both look identical in the console:

   ```bash
   nslookup -type=CNAME <validation-name>.auth.logan911.com 1.1.1.1
   ```

5. Confirm ACM status:

   ```powershell
   & 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' acm describe-certificate `
     --certificate-arn <AuthCertificateArn> `
     --region us-east-1 `
     --profile lcdash-sandbox-admin `
     --query 'Certificate.Status' `
     --output text
   ```

   Expected final status: `ISSUED`. `PENDING_VALIDATION` means remain at this
   hold and **do not create the Cognito custom domain** — creating a custom
   domain against a certificate that is not yet issued fails the deployment.

---

## Records that must NOT be created

- No A record for `aws.logan911.com` or `auth.logan911.com`. Both are `CNAME`
  targets — the application hostname points at the ALB, and the auth hostname
  will point at the CloudFront distribution Cognito creates.
- No Cloudflare proxy (orange cloud) on any of these. The application CNAME, the
  validation CNAMEs, and the eventual auth CNAME are all `DNS only`.
- No Route 53 record or hosted zone. Neither stack creates one, by design.

**One exception worth stating explicitly**, because it reads like a
contradiction: the **apex** `logan911.com` does have A records, pointing at the
Hostinger web host. Those are pre-existing, serve the county website, and must
stay. They also happen to satisfy a Cognito precondition — a custom auth domain
cannot be created unless its parent domain resolves — so the apex resolving is
required, not merely tolerated. The prohibition above is on adding A records for
the `aws` and `auth` subdomains, not on the apex.
