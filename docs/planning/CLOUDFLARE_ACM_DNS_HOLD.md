# Cloudflare ACM DNS hold

Current hold: the `aws.logan911.com` ACM certificate cannot become `ISSUED`
until its validation CNAME is published in authoritative Cloudflare DNS.
Hostinger is the registrar only. Do not change nameservers and do not add or
edit DNS records in Hostinger.

Create this exact Cloudflare record:

| Setting | Exact value |
|---|---|
| Type | `CNAME` |
| Name | `_313fa8e4125f9012d0e6bdfe254706cb.aws.logan911.com` |
| Target | `_ed12f3d542d86c927869d4ee9b325bfd.jkddzztszm.acm-validations.aws` |
| Proxy status | `DNS only` (gray cloud) |
| TTL | `Auto` is acceptable |

Do not create an A record, redirect, Cloudflare proxy, or Route 53 record.

After saving, verify public DNS read-only:

```powershell
Resolve-DnsName -Name _313fa8e4125f9012d0e6bdfe254706cb.aws.logan911.com -Type CNAME -DnsOnly
```

Expected target:
`_ed12f3d542d86c927869d4ee9b325bfd.jkddzztszm.acm-validations.aws`.

Then verify ACM read-only:

```powershell
& 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' acm describe-certificate `
  --certificate-arn arn:aws:acm:us-east-1:862772137583:certificate/847de721-b0f5-4c3d-8ec1-c27dd51a201d `
  --region us-east-1 `
  --profile lcdash-sandbox-admin `
  --query 'Certificate.Status' `
  --output text
```

Expected final status: `ISSUED`. `PENDING_VALIDATION` means remain at this hold;
do not deploy the foundation.
