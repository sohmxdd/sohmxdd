import datetime
from dateutil import relativedelta
import requests
import os
from lxml import etree
import time
import hashlib

PROFILE_CONFIG = {
    "USERNAME": "sohmxdd",
    "DISPLAY_NAME": "Soham Mishra",
    "TERMINAL_HEADER": "soham@localhost",
    "BIRTHDAY": (2006, 8, 3),  # (year, month, day) -> 3rd August 2006
    "UNIVERSITY": "DBS Global University",
    "INTERESTS": "AI Systems, Agentic Security, Backend Infrastructure, Open Source",
    "LANGUAGES": "Python, TypeScript, C++, SQL, MongoDB",
    "FRAMEWORKS": "FastAPI, LangGraph, React, Docker, PostgreSQL, Redis",
}

USER_NAME = os.environ.get("USER_NAME")
if not USER_NAME or USER_NAME.strip() == "":
    USER_NAME = PROFILE_CONFIG["USERNAME"]
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN")
HEADERS = {"authorization": f"token {ACCESS_TOKEN}"} if ACCESS_TOKEN else {}

QUERY_COUNT = {
    "user_getter": 0,
    "follower_getter": 0,
    "graph_repos_stars": 0,
    "graph_commits": 0,
    "loc_query": 0,
    "recursive_loc": 0,
}
OWNER_ID = None


def daily_readme(birthday: datetime.datetime) -> str:
    """Return the age in years, months, and days with birthday emoji if applicable."""
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    emoji = " 🎂" if diff.months == 0 and diff.days == 0 else ""
    return (
        f"{diff.years} year{'s' if diff.years != 1 else ''}, "
        f"{diff.months} month{'s' if diff.months != 1 else ''}, "
        f"{diff.days} day{'s' if diff.days != 1 else ''}{emoji}"
    )


def simple_request(func_name: str, query: str, variables: dict) -> requests.Response:
    """
    Send a GraphQL request and return the response.
    Raises a useful exception for HTTP errors, GraphQL errors, or malformed payloads.
    """
    if not ACCESS_TOKEN:
        raise Exception("ACCESS_TOKEN environment variable is missing. Cannot fetch GitHub statistics.")

    QUERY_COUNT[func_name] += 1
    r = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": variables},
        headers=HEADERS,
        timeout=30,
    )

    try:
        payload = r.json()
    except Exception:
        raise Exception(f"{func_name} non-json response: {r.status_code} {r.text}")

    if r.status_code != 200:
        raise Exception(f"{func_name} http error: {r.status_code} {payload}")

    # GraphQL can return 200 with errors
    if isinstance(payload, dict) and payload.get("errors"):
        raise Exception(f"{func_name} graphql errors: {payload['errors']}")

    if "data" not in payload:
        raise Exception(f"{func_name} missing data field: {payload}")

    return r


def graph_commits(start_date, end_date) -> int:
    """Fetch total contributions between start_date and end_date by looping through years."""
    start_dt = datetime.datetime.fromisoformat(start_date.replace("Z", "+00:00")).replace(tzinfo=None)
    end_dt = datetime.datetime.fromisoformat(end_date.replace("Z", "+00:00")).replace(tzinfo=None)

    total_contributions = 0
    current_start = start_dt

    while current_start < end_dt:
        current_end = current_start + relativedelta.relativedelta(years=1)
        if current_end > end_dt:
            current_end = end_dt

        query = """
        query($start_date: DateTime!, $end_date: DateTime!, $login: String!) {
            user(login: $login) {
                contributionsCollection(from: $start_date, to: $end_date) {
                    contributionCalendar { totalContributions }
                }
            }
        }"""
        variables = {
            "start_date": current_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date": current_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "login": USER_NAME,
        }
        data = simple_request("graph_commits", query, variables).json().get("data")
        if data and data.get("user"):
            total_contributions += int(
                data["user"]["contributionsCollection"]["contributionCalendar"][
                    "totalContributions"
                ]
            )

        current_start = current_end

    return total_contributions


def graph_repos_stars(count_type: str, owner_affiliation: list) -> int:
    """
    Fetch repository count or total stars for the user.
    Correctly paginates through all repositories.
    """
    query = """
    query($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges { node { stargazers { totalCount } } }
                pageInfo { endCursor hasNextPage }
            }
        }
    }"""

    cursor = None
    total_repos = None
    total_stars = 0

    while True:
        variables = {
            "owner_affiliation": owner_affiliation,
            "login": USER_NAME,
            "cursor": cursor,
        }
        data = simple_request("graph_repos_stars", query, variables).json()["data"][
            "user"
        ]["repositories"]

        if total_repos is None:
            total_repos = int(data["totalCount"])

        if count_type == "stars":
            total_stars += sum(
                edge["node"]["stargazers"]["totalCount"] for edge in data["edges"]
            )

        if not data["pageInfo"]["hasNextPage"]:
            break
        cursor = data["pageInfo"]["endCursor"]

    if count_type == "repos":
        return total_repos
    if count_type == "stars":
        return total_stars
    raise ValueError("count_type must be 'repos' or 'stars'")


def recursive_loc(owner, repo_name, cursor=None, additions=0, deletions=0, commits=0):
    """Recursively calculate LOC and commits for a repository."""
    QUERY_COUNT["recursive_loc"] += 1
    query = """
    query($owner: String!, $repo_name: String!, $cursor: String, $author_id: ID) {
        repository(owner: $owner, name: $repo_name) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor, author: {id: $author_id}) {
                            edges {
                                node { additions deletions author { user { id } } }
                            }
                            pageInfo { hasNextPage endCursor }
                        }
                    }
                }
            }
        }
    }"""
    variables = {"owner": owner, "repo_name": repo_name, "cursor": cursor, "author_id": OWNER_ID}
    repo = simple_request("recursive_loc", query, variables).json()["data"][
        "repository"
    ]

    if not repo or not repo.get("defaultBranchRef"):
        return additions, deletions, commits

    history = repo["defaultBranchRef"]["target"]["history"]

    for edge in history["edges"]:
        node = edge["node"]
        author = node.get("author")
        user = author.get("user") if author else None
        if user and user.get("id") == OWNER_ID:
            commits += 1
            additions += int(node.get("additions", 0))
            deletions += int(node.get("deletions", 0))

    if not history["edges"] or not history["pageInfo"]["hasNextPage"]:
        return additions, deletions, commits

    return recursive_loc(
        owner,
        repo_name,
        history["pageInfo"]["endCursor"],
        additions,
        deletions,
        commits,
    )


def loc_pipeline():
    """Compute LOC across all repositories using cache and recursive LOC counting."""
    filename = f"cache/{hashlib.sha256(USER_NAME.encode()).hexdigest()}.txt"
    os.makedirs("cache", exist_ok=True)

    query = """
    query($login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor,
              ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]) {
                edges {
                    node {
                        nameWithOwner
                        defaultBranchRef {
                            target {
                                ... on Commit {
                                    history {
                                        totalCount
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo { endCursor hasNextPage }
            }
        }
    }"""

    edges = []
    cursor = None
    while True:
        response = simple_request(
            "loc_query", query, {"login": USER_NAME, "cursor": cursor}
        ).json()["data"]["user"]["repositories"]
        edges.extend(response["edges"])
        if not response["pageInfo"]["hasNextPage"]:
            break
        cursor = response["pageInfo"]["endCursor"]

    # Load cache
    cache_dict = {}
    if os.path.exists(filename):
        with open(filename, "r") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) == 5:
                    cache_dict[parts[0]] = (int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]))
                elif len(parts) == 4:
                    cache_dict[parts[0]] = (-1, int(parts[1]), int(parts[2]), int(parts[3]))

    new_cache_lines = []
    additions_total, deletions_total, commits_total = 0, 0, 0

    for repo in edges:
        name_with_owner = repo["node"]["nameWithOwner"]
        owner, repo_name = name_with_owner.split("/", 1)
        repo_hash = hashlib.sha256(name_with_owner.encode()).hexdigest()

        # Get total commits on default branch
        ref = repo["node"].get("defaultBranchRef")
        total_commits = 0
        if ref and ref.get("target"):
            history = ref["target"].get("history")
            if history:
                total_commits = int(history.get("totalCount", 0))

        # Check cache
        cached_val = cache_dict.get(repo_hash)
        if cached_val and cached_val[0] == total_commits and total_commits > 0:
            # Commit count hasn't changed, use cached LOC stats
            _, my_commit, add, delete = cached_val
            print(f"LOC (cached) for {name_with_owner}: {my_commit} commits")
        else:
            # Recalculate LOC stats
            print(f"Fetching LOC (uncached) for {name_with_owner}...")
            try:
                add, delete, my_commit = recursive_loc(owner, repo_name)
            except Exception as e:
                print(f"Warning: could not fetch LOC for {name_with_owner}: {e}")
                # Fallback to cache if available
                if cached_val:
                    _, my_commit, add, delete = cached_val
                else:
                    add, delete, my_commit = 0, 0, 0

        additions_total += add
        deletions_total += delete
        commits_total += my_commit

        new_cache_lines.append(
            f"{repo_hash} {total_commits} {my_commit} {add} {delete}\n"
        )

    with open(filename, "w") as f:
        f.writelines(new_cache_lines)

    return (
        additions_total,
        deletions_total,
        additions_total - deletions_total,
        commits_total,
    )


def get_cached_loc():
    """Read totals from the cache file instead of querying GitHub API."""
    filename = f"cache/{hashlib.sha256(USER_NAME.encode()).hexdigest()}.txt"
    if not os.path.exists(filename):
        return (0, 0, 0, 0)

    additions, deletions, commits = 0, 0, 0
    with open(filename, "r") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) == 5:
                commits += int(parts[2])
                additions += int(parts[3])
                deletions += int(parts[4])
            elif len(parts) == 4:
                commits += int(parts[1])
                additions += int(parts[2])
                deletions += int(parts[3])
    return additions, deletions, additions - deletions, commits


def justify_format(root, element_id, new_text) -> bool:
    """
    Replace text content of an SVG element by id.
    If the element contains tspans, updates the first tspan.
    """
    new_text = str(new_text)
    elems = root.xpath(f"//*[@id='{element_id}']")
    if not elems:
        return False

    el = elems[0]
    tspans = el.findall(".//{*}tspan")
    if tspans:
        tspans[0].text = new_text
    else:
        el.text = new_text
    return True


def svg_overwrite(
    filenames,
    age_data,
    commit_data,
    star_data,
    repo_data,
    contrib_data,
    follower_data,
    loc_data,
):
    """
    Update SVG text elements by id, including LOC with added/deleted stats.
    Also updates static profile parameters from PROFILE_CONFIG.
    """
    if isinstance(filenames, str):
        filenames = [filenames]

    for filename in filenames:
        if not os.path.exists(filename):
            continue

        parser = etree.XMLParser(remove_blank_text=False)
        tree = etree.parse(filename, parser)
        root = tree.getroot()

        # Update static profile configuration elements
        for config_key, elem_id in [
            ("TERMINAL_HEADER", "header_title"),
            ("UNIVERSITY", "university_val"),
            ("INTERESTS", "interests_val"),
            ("LANGUAGES", "languages_val"),
            ("FRAMEWORKS", "frameworks_val"),
        ]:
            if config_key in PROFILE_CONFIG:
                justify_format(root, elem_id, PROFILE_CONFIG[config_key])

        # Update dynamic statistics
        for elem_id, val in zip(
            [
                "age_data",
                "commit_data",
                "star_data",
                "repo_data",
                "contrib_data",
                "follower_data",
            ],
            [age_data, commit_data, star_data, repo_data, contrib_data, follower_data],
        ):
            justify_format(root, elem_id, val)

        # LOC special handling
        if loc_data:
            main_loc, added, deleted = loc_data[2], loc_data[0], loc_data[1]
            justify_format(root, "loc_data", f"{main_loc:,}")
            added_deleted_text = f"(+{added:,} / -{deleted:,})"

            loc_elem = root.xpath("//*[@id='loc_data']")
            if loc_elem:
                parent = loc_elem[0].getparent()
                # Find existing suffix tspan or append
                for ts in parent.findall(".//{*}tspan"):
                    style = ts.attrib.get("style", "")
                    # Match by typical fill style in dark/light templates
                    if "fill" in style:
                        ts.text = added_deleted_text
                        break
                else:
                    new_ts = etree.Element("tspan")
                    new_ts.text = added_deleted_text
                    new_ts.attrib["dx"] = "12"
                    # Determine color based on light/dark mode
                    if "light" in filename:
                        new_ts.attrib["style"] = "fill:#7d5fff"
                    else:
                        new_ts.attrib["style"] = "fill:#bb9af7"
                    parent.append(new_ts)

        temp_filename = filename + ".tmp"
        tree.write(temp_filename, encoding="utf-8", xml_declaration=True)
        os.replace(temp_filename, filename)


def user_getter(username):
    """Fetch GitHub user ID and account creation date."""
    query = "query($login: String!){ user(login: $login) { id createdAt } }"
    data = simple_request("user_getter", query, {"login": username}).json()["data"][
        "user"
    ]
    return data["id"], data["createdAt"]


def follower_getter(username):
    """Fetch follower count."""
    query = "query($login: String!){ user(login: $login) { followers { totalCount } } }"
    return int(
        simple_request("follower_getter", query, {"login": username}).json()["data"][
            "user"
        ]["followers"]["totalCount"]
    )


def perf_counter(func, *args):
    """Measure execution time of a function."""
    start = time.perf_counter()
    result = func(*args)
    return result, time.perf_counter() - start


def is_tuesday():
    """Return True if today is Tuesday."""
    return datetime.datetime.today().weekday() == 1


if __name__ == "__main__":
    b_y, b_m, b_d = PROFILE_CONFIG["BIRTHDAY"]
    birthday_dt = datetime.datetime(b_y, b_m, b_d)

    try:
        user_id, acc_date = user_getter(USER_NAME)
        OWNER_ID = user_id

        age_data, t_age = perf_counter(daily_readme, birthday_dt)
        print(f"Age calculation: {t_age:.4f}s")

        stars, t_stars = perf_counter(
            graph_repos_stars, "stars", ["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"]
        )
        print(f"Star count: {t_stars:.4f}s")

        repos, t_repos = perf_counter(
            graph_repos_stars, "repos", ["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"]
        )
        print(f"Repo count: {t_repos:.4f}s")

        followers, t_followers = perf_counter(follower_getter, USER_NAME)
        print(f"Follower count: {t_followers:.4f}s")

        commits, t_commits = perf_counter(
            graph_commits, acc_date, datetime.datetime.utcnow().isoformat()
        )
        print(f"Commit count: {t_commits:.4f}s")

        cache_path = f"cache/{hashlib.sha256(USER_NAME.encode()).hexdigest()}.txt"
        if is_tuesday() or not os.path.exists(cache_path):
            loc_data, t_loc = perf_counter(loc_pipeline)
            print(f"LOC count (pipeline): {t_loc:.4f}s")
        else:
            loc_data, t_loc = perf_counter(get_cached_loc)
            print(f"LOC count (cached): {t_loc:.4f}s")

    except Exception as e:
        print(f"Error encountered: {e}")
        # Keep variables defined for svg_overwrite fallback
        age_data = daily_readme(birthday_dt)
        stars = repos = followers = commits = 0
        loc_data = (0, 0, 0, 0)

    svg_overwrite(
        ["dark_mode.svg", "light_mode.svg"],
        age_data,
        commits,
        stars,
        repos,
        commits,  # Contributed (original logic uses commits here as placeholder)
        followers,
        loc_data,
    )
