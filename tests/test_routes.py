def test_index_page_returns_200(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.content_type


def test_index_page_contains_navigation(client):
    response = client.get("/")

    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Strona główna" in body
    assert "Do Podpisania" in body or "Do podpisania" in body


def test_documents_to_sign_page_returns_200(client):
    response = client.get("/documents-to-sign")

    if response.status_code == 404:
        response = client.get("/documents_to_sign")

    assert response.status_code in (200, 302)


def test_form_page_returns_200(client):
    response = client.get("/form/sample_form")

    if response.status_code == 404:
        response = client.get("/sample_form")

    assert response.status_code in (200, 302)