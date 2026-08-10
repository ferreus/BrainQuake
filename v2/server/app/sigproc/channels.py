"""SEEG channel selection. mne + stdlib only."""
import logging
import re

import mne

logger = logging.getLogger(__name__)

# An SEEG contact label is a shaft letter, optionally primed for the
# contralateral shaft, then the contact number -- A1, D6, X'12, G'10. Anything
# else in the file is an auxiliary trace.
#
# Identifying contacts positively rather than blacklisting known auxiliary
# names is what makes this reliable: a blacklist has to anticipate every
# amplifier's naming (REF1, DC01, EKG1, UNUSED248 and a bare E all appear in
# one Nihon Kohden export), while the contact convention is fixed by the
# implantation scheme.
_SEEG_CONTACT_RE = re.compile(r"^[A-Za-z]'?\d+$")


def seeg_contacts(chn_names):
    """The SEEG contacts among `chn_names`, in the recording's own order.

    Returns every name unchanged if none match: that means the file follows a
    different convention (CH1..CH64, bipolar pairs), not that it holds no brain
    signal, so the choice goes back to the user.
    """
    contacts = [n for n in chn_names if _SEEG_CONTACT_RE.match(str(n).strip())]
    if not contacts:
        logger.warning(
            "no channel is named like an SEEG contact; keeping all %d -- first few are %s",
            len(chn_names), list(chn_names)[:6],
        )
        return list(chn_names)
    return contacts


def load_seeg(edf_path, include_aux=False):
    """Open an EDF holding only its SEEG contacts.

    Dropping aux traces here, once, is what keeps them out of every module's
    common-average reference -- a DC input in mV or an unitless mark word sits
    10^3-10^6 above a microvolt contact and swamps the average. preload=False,
    so callers can crop before pulling samples.
    """
    raw = mne.io.read_raw_edf(edf_path, preload=False, stim_channel=None)
    if include_aux:
        return raw
    contacts = seeg_contacts(raw.ch_names)
    dropped = [n for n in raw.ch_names if n not in set(contacts)]
    if dropped:
        logger.info("excluding %d auxiliary channel(s): %s", len(dropped), dropped)
        raw.pick(contacts)
    return raw
