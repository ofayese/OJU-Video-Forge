import json

with open('trivy_render_worker_reconciliation.json') as f:
    content = f.read()

json_start = content.find('{')
data = json.loads(content[json_start:])

results = data.get('Results', [])
high_count = 0
critical_count = 0
for r in results:
    vulns = r.get('Vulnerabilities', [])
    for v in vulns:
        if v.get('Severity') == 'HIGH':
            high_count += 1
        elif v.get('Severity') == 'CRITICAL':
            critical_count += 1

print(f"HIGH: {high_count}")
print(f"CRITICAL: {critical_count}")

# Also list unique CVEs
cves = set()
for r in results:
    vulns = r.get('Vulnerabilities', [])
    for v in vulns:
        if v.get('Severity') in ('HIGH', 'CRITICAL'):
            cves.add(v['VulnerabilityID'])

print(f"\nUnique CVEs ({len(cves)}):")
for cve in sorted(cves):
    print(f"  {cve}")
