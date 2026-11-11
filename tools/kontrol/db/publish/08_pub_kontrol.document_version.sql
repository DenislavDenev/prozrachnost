SELECT v.record_id, v.sha256, d.url, v.fetched_at FROM kontrol.document_version v JOIN kontrol.document d USING (record_id)
