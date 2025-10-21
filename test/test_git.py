from gitq.git import split_author


def test_split():

    name, email, date = split_author("Lawrence D'Anna <larry@elder-gods.org> 1760365949 -0400")
    assert name == "Lawrence D'Anna"
    assert email == "larry@elder-gods.org"
    assert date == "1760365949 -0400"

    name, email, date = split_author("Lawrence D'Anna <larry@elder-gods.org> 1760365949 +0400")
    assert name == "Lawrence D'Anna"
    assert email == "larry@elder-gods.org"
    assert date == "1760365949 +0400"

    name, email, date = split_author("Lawrence D'Anna <larry@elder-gods.org> 1760365949 0000")
    assert name == "Lawrence D'Anna"
    assert email == "larry@elder-gods.org"
    assert date == "1760365949 0000"
