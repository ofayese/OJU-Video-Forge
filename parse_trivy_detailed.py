import json

with open('docs/reviews/evidence/2026-11-10-narration-remediation/trivy_baseline.json', 'r') as f:
    data = json.load(f)

print('Image:', data.get('ArtifactName'))
print('ImageID:', data.get('Metadata',{}).get('ImageID'))
print('OS:', data.get('Metadata',{}).get('OS'))
print('CreatedAt:', data.get('CreatedAt'))

results = data.get('Results', [])
high = 0
critical = 0
cves = set()
packages = {}

for r in results:
    for v in r.get('Vulnerabilities', []):
        sev = v.get('Severity', '')
        if sev == 'HIGH':
            high += 1
        elif sev == 'CRITICAL':
            critical += 1
        cve_id = v.get('VulnerabilityID', '')
        cves.add(cve_id)
        pkg = v.get('PkgName', '')
        installed = v.get('InstalledVersion', '')
        fixed = v.get('FixedVersion', '')
        title = v.get('Title', '')
        primary_url = v.get('PrimaryURL', '')
        if pkg:
            if pkg not in packages:
                packages[pkg] = {'high': 0, 'critical': 0, 'cves': set(), 'installed': installed, 'fixed': fixed, 'details': []}
            packages[pkg]['cves'].add(cve_id)
            packages[pkg]['details'].append({
                'cve': cve_id,
                'severity': sev,
                'title': title,
                'fixed': fixed,
                'url': primary_url
            })
            if sev == 'HIGH':
                packages[pkg]['high'] += 1
            elif sev == 'CRITICAL':
                packages[pkg]['critical'] += 1

print(f'\nHIGH: {high}, CRITICAL: {critical}, Unique CVEs: {len(cves)}')
print(f'Total affected packages: {len(packages)}')
print()

# Sort by severity count
sorted_packages = sorted(packages.items(), key=lambda x: (x[1]['critical'], x[1]['high']), reverse=True)

for pkg, info in sorted_packages:
    print(f'{pkg} (installed: {info["installed"]}, fixed: {info["fixed"] or "N/A"}): HIGH={info["high"]}, CRITICAL={info["critical"]}')
    for d in info['details']:
        print(f'  {d["cve"]} [{d["severity"]}] - {d["title"][:80]}...')
        print(f'    Fixed: {d["fixed"] or "N/A"}')
        print(f'    URL: {d["url"]}')

# Save detailed CSV
import csv
with open('docs/reviews/evidence/2026-11-10-narration-remediation/vuln_inventory.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['Package', 'InstalledVersion', 'FixedVersion', 'CVE', 'Severity', 'Title', 'PrimaryURL'])
    for pkg, info in packages.items():
        for d in info['details']:
            writer.writerow([pkg, info['installed'], d['fixed'] or 'N/A', d['cve'], d['severity'], d['title'], d['url']])

print('\nInventory saved to vuln_inventory.csv')