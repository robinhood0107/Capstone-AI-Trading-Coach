# Local secrets

secret 값은 이 README를 제외하고 Git에 들어가지 않는다. 파일을 사용하는 경우 mode
`0600`, regular-file, owner, size, directory-fd와 `O_NOFOLLOW`를 검증하며 값·존재 여부·경로
세부를 API, 로그, metric, 예외, manifest에 노출하지 않는다.
