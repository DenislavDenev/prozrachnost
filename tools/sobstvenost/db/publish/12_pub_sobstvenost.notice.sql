-- a concessionaire who is not a legal entity (a sole trader, a registered farmer, a person) is not named here
SELECT concession_id, position, source_url, location_original,
       CASE WHEN concessionaire_is_person THEN 'Физическо лице или едноличен търговец: името не се преиздава' ELSE concessionaire_name END,
       concessionaire_eik, term_months
FROM sobstvenost.notice
