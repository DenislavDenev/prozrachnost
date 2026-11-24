-- the members of boards in their public role (persons.toml: public-money, full name)
SELECT appk_id, sha256, position, member_name::text::pub.role_name, country, entry::text::pub.role_name FROM sobstvenost.board_entry
