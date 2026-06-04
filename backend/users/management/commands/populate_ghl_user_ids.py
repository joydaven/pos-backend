from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from users.models import UserProfile


GHL_USER_ID_MAP = {
    "alexr@doctorsstudio.com": "ceQKJjfq9Y1ZEWckirMM",
    "abbie@doctorsstudio.com": "iBiO4wVuyfsoc6gu184g",
    "abbey@doctorsstudio.com": "zf4rJ6k5TVqKFc854dmT",
    "administrative1@doctorsstudio.com": "nLIeGxftyN7yz5PsLZCP",
    "alex@doctorsstudio.com": "eiBJSpYLbmxjRXqjwr8o",
    "ana@doctorsstudio.com": "m7v0fXpHl2kgikK7rGsI",
    "angelo@doctorsstudio.com": "BpFdnWbfv5QKYH3UHc45",
    "coach@doctorsstudio.com": "idIqCP4zxCh1jrbROPyO",
    "boca@doctorsstudio.com": "n0iIeqykBy3FvxE4gy5g",
    "cassandra@doctorsstudio.com": "PEsZip3bJ7Ost1u0fLLS",
    "chicago@doctorsstudio.com": "hEhvoMJpy6Zyfz0hPz1Z",
    "chris@doctorsstudio.com": "iesOQI0VJjPOuE9prBPM",
    "clinical@doctorsstudio.com": "ghfMsyDpf4SAuPgdvthP",
    "healthcoach@doctorsstudio.com": "VdC1gK1Q209IJEXPg8Ov",
    "vina@doctorsstudio.com": "sXqIqQBOLviwComCyjyf",
    "forms@doctorsstudio.com": "WY45ip3gYiAd6ikgGkVN",
    "frontgroup@doctorsstudio.com": "eLF9GImMre5obpsrYSQt",
    "gemma@doctorsstudio.com": "qxTUAUHg04bcgjSEg5b7",
    "gerry@doctorsstudio.com": "3Ke46F7RFGmOq2InPZZG",
    "heidi@doctorsstudio.com": "No2nN5pVGqklon4GEfWf",
    "jay@doctorsstudio.com": "JHJL8aczY57AEU8t3CMy",
    "jcdevera@doctorsstudio.com": "ffiP1RSewDszSqfrtHWX",
    "jennifer@doctorsstudio.com": "i5I4cl6bGF6nf7isnX2x",
    "jennifer.chicago@doctorsstudio.com": "7y0EXPADlZfgw5fRUY1I",
    "jenniferjupiter@doctorsstudio.com": "1292Q1iBQkP9etUjdlTl",
    "jeremy@doctorsstudio.com": "FINg4to45dyLR0xkLpze",
    "john.b@doctorsstudio.com": "shT7iuzLzienGmHjHghw",
    "joyhouston@gmail.com": "ljvqpMYwsucDlunrkBII",
    "joy@doctorsstudio.com": "5uzMqhLpBDvkLqy62FYG",
    "julia@doctorsstudio.com": "ZjpKleNZMfU1O6eejY9f",
    "juliana@doctorsstudio.com": "O6g6g8HoVAGt0cwFkVBV",
    "jupiter@doctorsstudio.com": "h4hn98IUqh7Dz7IvdPgS",
    "justin@doctorsstudio.com": "dEmwgi0K4VqJgrs2buIE",
    "karla@doctorsstudio.com": "BLYDuds51En597i4HMag",
    "kathleen@doctorsstudio.com": "CjL8vn6D5yRzB5VuJUmV",
    "kathy@doctorsstudio.com": "9x7iFByRZXu041xG1RbI",
    "kay@doctorsstudio.com": "L67oJH4hwH57uNsaqXfB",
    "kristine@doctorsstudio.com": "ldf4jex0zUQ6rCJVFOXa",
    "kristinesenas@doctorsstudio.com": "KIATZ3V9f4xfWgXybMgm",
    "lauren@doctorsstudio.com": "FJuXDjx5nRyClTkATR67",
    "leshly@doctorsstudio.com": "WnWEYpJg7ufdpEBn8YFB",
    "lidia@doctorsstudio.com": "0YJspplSiFI0QyHOixS7",
    "drroy@doctorsstudio.com": "gzmB7niBjT9pp9bgeYrW",
    "logan@valdaeon.com": "vCXkZq42PfPqxD6brmki",
    "logan@doctorsstudio.com": "tTXI4yoYxEJWXiOMkkrL",
    "malica@doctorsstudio.com": "1uxpfkvm37Jb2CCJjNkE",
    "michelle.a@doctorsstudio.com": "0ly3deMTiraFOUfZaaSz",
    "michelle.w@doctorsstudio.com": "883c6GM7H92OG1x4TtRn",
    "patrick@doctorsstudio.com": "UJnU2bX9eaW0m69Nddrk",
    "rachelle@doctorsstudio.com": "bNxUkaRMAIagVU9xhLwQ",
    "rama@appypiellp.com": "ozl0rAD3Wo7iggwiYWyL",
    "raquel@doctorsstudio.com": "6xaz5ws9tnCSH3sJHEn0",
    "reah@doctorsstudio.com": "Q6lnrwZPfGjg6lpzBfzT",
    "sandy@doctorsstudio.com": "CgeaXiisz6V7a5D7bqr1",
    "sandyjupiter@doctorsstudio.com": "xaK2z95pyDCuqXbSq3TT",
    "sean@doctorsstudio.com": "zRpxycH7D2WPpjkRg95u",
    "shawna@doctorsstudio.com": "3txZl2lqu9PQTyVDQ3uQ",
    "specialist@doctorsstudio.com": "kqyW824HaPpV9Vs5x8yv",
    "support@doctorsstudio.com": "COQf9s2GLG9udGP2ZGKG",
    "tariq@doctorsstudio.com": "deBtIPJJottbkMNbbTbo",
    "tariq@secureyourwellness.com": "c6ou4cjoidjrKeaZ79Ah",
    "testgroup@doctorsstudio.com": "jg0mRhvoBx13Tcx5YqvG",
    "testguy@doctorsstudio.com": "2fnmAfgZ0tSkLbv3c6nO",
    "tools@doctorsstudio.com": "NXLWHzYg4m5IIXlvaDhi",
    "training@doctorsstudio.com": "BgHKqz6VFfuXgcBUNX3v",
    "travis@adpushmedia.com": "ipzrxdZNJGvUTWnomA9P",
}


class Command(BaseCommand):
    help = 'Populate GHL User IDs for POS users based on email mapping'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        updated = 0
        skipped = 0
        not_found = 0

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be made\n'))

        for email, ghl_id in GHL_USER_ID_MAP.items():
            try:
                user = User.objects.get(email__iexact=email)
                profile, _ = UserProfile.objects.get_or_create(user=user)

                if profile.ghl_user_id == ghl_id:
                    self.stdout.write(f'  SKIP {email} — already set to {ghl_id}')
                    skipped += 1
                    continue

                if not dry_run:
                    profile.ghl_user_id = ghl_id
                    profile.save(update_fields=['ghl_user_id'])

                self.stdout.write(self.style.SUCCESS(
                    f'  SET  {email} → {ghl_id}'
                ))
                updated += 1

            except User.DoesNotExist:
                self.stdout.write(self.style.WARNING(
                    f'  MISS {email} — no POS user with this email'
                ))
                not_found += 1

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Updated: {updated}'))
        self.stdout.write(f'Skipped (already set): {skipped}')
        self.stdout.write(self.style.WARNING(f'Not found in POS: {not_found}'))
