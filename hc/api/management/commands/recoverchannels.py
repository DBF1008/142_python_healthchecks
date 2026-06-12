from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils.timezone import now

from hc.api.models import MAX_RECOVERY_ATTEMPTS, Channel
from hc.lib.statsd import statsd


class Command(BaseCommand):
    help = """Re-enable channels that were disabled by a permanent error.

    Uses exponential backoff: a disabled channel is re-enabled once its backoff
    interval (derived from disabled_at and the number of prior recovery
    attempts) has elapsed. The channel then gets one more chance to deliver on
    the next real notification; if it fails again it is disabled once more, with
    a longer backoff. After MAX_RECOVERY_ATTEMPTS attempts the channel is left
    disabled and must be re-enabled manually.
    """

    def handle(self, **options: Any) -> str:
        recovered = 0
        waiting = 0
        capped = 0

        frozen_now = now()
        q = Channel.objects.filter(disabled=True, disabled_at__isnull=False)
        for channel in q:
            if channel.recovery_attempts >= MAX_RECOVERY_ATTEMPTS:
                # Exhausted automatic recovery; needs manual intervention.
                capped += 1
                continue

            due = channel.next_recovery_at()
            # due is None only when capped or disabled_at is None; both cases are
            # already excluded above and by the queryset filter.
            assert due is not None
            if frozen_now < due:
                waiting += 1
                continue

            attempt = channel.recovery_attempts + 1
            # Clear disabled_at so that, if this channel fails again, notify()
            # records a fresh disable time while recovery_attempts keeps growing,
            # producing a longer backoff next time.
            Channel.objects.filter(id=channel.id).update(
                disabled=False,
                disabled_at=None,
                recovery_attempts=attempt,
            )
            recovered += 1
            statsd.incr("hc.recoverchannels.recovered")
            self.stdout.write(
                f"Re-enabled {channel.code} ({channel.kind}), attempt {attempt}"
            )

        if capped:
            statsd.incr("hc.recoverchannels.capped", capped)

        return (
            f"Done!\n"
            f"* Re-enabled: {recovered}\n"
            f"* Waiting for backoff: {waiting}\n"
            f"* Capped (manual fix needed): {capped}\n"
        )
