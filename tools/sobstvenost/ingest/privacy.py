"""Whether the concessionaire a notice names is a legal entity.

The National Concession Register names companies, municipalities, sole traders, registered farmers and, now and then,
a person. The name of a concessionaire who is not a legal entity is the name of a private person in a public register:
it stays on the ingest side and is replaced in the serving layer (tools/danni/db/persons.toml). A sole trader (ЕТ) and
a registered farmer (ЗП) are persons; so is a name with no mark of a legal entity, because a company that forgot its
form costs less than a person shown by mistake."""
import re

PERSON_FORM = re.compile(r'^\W*(ЕТ|ЗП)(?![А-Яа-яA-Za-z])')
LEGAL_FORM = re.compile(r'(?<![А-Яа-яA-Za-z])(АД|ЕАД|ООД|ЕООД|КД|КДА|СД|ДЗЗД|АДСИЦ|ЕАДСИЦ|ДП|AD|EAD|OOD|EOOD|LTD|Ltd|GmbH|LLC)'
                        r'(?![А-Яа-яA-Za-z])|(?i:община|кооперация|сдружение|фондация|държавно предприятие)')


def is_person(name):
    """True when the name is not that of a legal entity. No name is not a person."""
    if not name or not name.strip():
        return False
    return bool(PERSON_FORM.match(name)) or not LEGAL_FORM.search(name)
