-- Quarantine only the byte-exact November 2025 source discovered invalid by the balance guard.
-- Its raw file and audit records remain available; a corrected source version is imported normally.
INSERT INTO ops.change_log(source,ref,field,old,new,cause)
SELECT 'kfp',ref,'snapshot',period::text,'Източниково салдо не се сверява','invalid'
FROM live.snapshot WHERE ref='f4fca4e3-a1e4-4f83-be11-776286dd8666'
AND EXISTS (SELECT 1 FROM src.resource s WHERE s.ref=live.snapshot.ref AND s.sha256='4eedc15acdbc6dac5eec80a509ebad4f9f3fd477ca183053035798252d55336f');
DELETE FROM live.snapshot l USING src.resource s WHERE l.ref=s.ref
AND s.ref='f4fca4e3-a1e4-4f83-be11-776286dd8666' AND s.sha256='4eedc15acdbc6dac5eec80a509ebad4f9f3fd477ca183053035798252d55336f';
UPDATE src.resource SET status='невалиден отговор',error='Приходи, разходи и нетни трансфери не съвпадат със салдото на европейските средства'
WHERE ref='f4fca4e3-a1e4-4f83-be11-776286dd8666' AND sha256='4eedc15acdbc6dac5eec80a509ebad4f9f3fd477ca183053035798252d55336f';
