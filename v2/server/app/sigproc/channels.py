"""SEEG channel selection. mne + stdlib only."""
import logging
import re

import mne

logger = logging.getLogger(__name__)

# An SEEG contact label is a shaft name, optionally primed for the
# contralateral shaft, then the contact number -- A1, D6, X'12, G'10, et'7.
# Anything else in the file is an auxiliary trace.
#
# Identifying contacts positively rather than blacklisting known auxiliary
# names is what makes this reliable: a blacklist has to anticipate every
# amplifier's naming (REF1, DC01, EKG1, UNUSED248 and a bare E all appear in
# one Nihon Kohden export), while the contact convention is fixed by the
# implantation scheme.
#
# A two-letter shaft is only recognised when primed (Grenoble's et'): unprimed
# two-letter names are indistinguishable from aux traces like DC01/CH1.
# Override via `pattern` for a scheme this does not fit.
DEFAULT_SEEG_CONTACT_PATTERN = r"^([A-Za-z]|[A-Za-z]{1,2}')\d+$"


def seeg_contacts(chn_names, pattern=None):
    """The SEEG contacts among `chn_names`, in the recording's own order.

    Returns every name unchanged if none match: that means the file follows a
    different convention (CH1..CH64, bipolar pairs), not that it holds no brain
    signal, so the choice goes back to the user.
    """
    contact_re = re.compile(pattern or DEFAULT_SEEG_CONTACT_PATTERN)
    contacts = [n for n in chn_names if contact_re.match(str(n).strip())]
    if not contacts:
        logger.warning(
            "no channel is named like an SEEG contact; keeping all %d -- first few are %s",
            len(chn_names), list(chn_names)[:6],
        )
        return list(chn_names)
    return contacts


def load_seeg(edf_path, include_aux=False, pattern=None):
    """Open an EDF holding only its SEEG contacts.

    Dropping aux traces here, once, is what keeps them out of every module's
    common-average reference -- a DC input in mV or an unitless mark word sits
    10^3-10^6 above a microvolt contact and swamps the average. preload=False,
    so callers can crop before pulling samples.
    """
    raw = mne.io.read_raw_edf(edf_path, preload=False, stim_channel=None)
    if include_aux:
        return raw
    contacts = seeg_contacts(raw.ch_names, pattern=pattern)
    dropped = [n for n in raw.ch_names if n not in set(contacts)]
    if dropped:
        logger.info("excluding %d auxiliary channel(s): %s", len(dropped), dropped)
        raw.pick(contacts)
    return raw
