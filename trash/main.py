import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import json
from getpass import getpass


# ---------------------------------------------------------------------
# SENDING REQUEST TO THE SHAMIM WEB PAGE AND UPDATE THE RECEIVER STATUS 
# ---------------------------------------------------------------------

SITE_URL = (
    "https://shamim.ssaa.ir/"
    "sbc/User/SiteMap/SiteMap"
)

GET_MODIFIED_SITES_URL = (
    "https://shamim.ssaa.ir/"
    "sbc/User/SiteMap/GetModifiedSites"
)

TIMEOUT = 30


# --------------------------------------------------
# SHAMIM client
# --------------------------------------------------

class ShamimClient:

    """
    Handle communication with SHAMIM.
    """

    STATUS_MAP = {
    2: "DISCONNECTED",
    3: "FIXED"
    }

    def __init__(
        self,
        username,
        password
    ):

        self.username = username
        self.password = password

        self.session = requests.Session()

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0 Safari/537.36"
            ),

            "Accept-Language": (
                "fa-IR,fa;q=0.9,en;q=0.8"
            )
        })

        self.authenticated = False


    # --------------------------------------------------
    # Login
    # --------------------------------------------------

    def login(self):
        """
        Authenticate to SHAMIM.
        """

        response = self.session.get(
            SITE_URL,
            timeout=TIMEOUT
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        password_input = soup.select_one(
            "input[type='password']"
        )

        if password_input is None:

            self.authenticated = True
            return


        form = password_input.find_parent(
            "form"
        )

        if form is None:

            raise RuntimeError(
                "Login form was not found."
            )


        login_url = urljoin(
            response.url,
            form.get("action") or response.url
        )

        payload = {}


        # Preserve hidden form fields
        for element in form.select(
            "input[name]"
        ):

            name = element.get(
                "name"
            )

            if name:

                payload[name] = element.get(
                    "value",
                    ""
                )


        username_input = None


        for element in form.select(
            "input[name]"
        ):

            input_type = (
                element.get("type")
                or "text"
            ).lower()

            name = (
                element.get("name")
                or ""
            ).lower()

            element_id = (
                element.get("id")
                or ""
            ).lower()


            if input_type in [
                "hidden",
                "password",
                "submit",
                "button"
            ]:
                continue


            if (
                "user" in name
                or "user" in element_id
                or "username" in name
                or "username" in element_id
            ):

                username_input = element
                break


        if username_input is None:

            username_input = form.select_one(
                "input[type='text'][name]"
            )


        if username_input is None:

            raise RuntimeError(
                "Username field was not found."
            )


        payload[
            username_input.get("name")
        ] = self.username

        payload[
            password_input.get("name")
        ] = self.password


        response = self.session.post(
            login_url,
            data=payload,
            timeout=TIMEOUT,
            allow_redirects=True
        )

        response.raise_for_status()


        # Verify login
        response = self.session.get(
            SITE_URL,
            timeout=TIMEOUT
        )

        response.raise_for_status()

        check_soup = BeautifulSoup(
            response.text,
            "html.parser"
        )


        if check_soup.select_one(
            "input[type='password']"
        ):

            raise RuntimeError(
                "SHAMIM login failed."
            )


        self.authenticated = True


    # --------------------------------------------------
    # Verification token
    # --------------------------------------------------

    def get_verification_token(self):
        """
        Extract the AJAX verification token.
        """

        response = self.session.get(
            SITE_URL,
            timeout=TIMEOUT
        )

        response.raise_for_status()

        html = response.text


        pattern = (
            r"setRequestHeader\("
            r"['\"]RequestVerificationToken['\"]"
            r"\s*,\s*"
            r"['\"]([^'\"]+)['\"]"
        )


        match = re.search(
            pattern,
            html,
            re.IGNORECASE
        )


        if not match:

            raise RuntimeError(
                "RequestVerificationToken "
                "was not found."
            )


        return match.group(1)


    # --------------------------------------------------
    # Get all station data
    # --------------------------------------------------

    def get_all_stations(self):
        """
        Download current SHAMIM station information.
        """

        if not self.authenticated:
            self.login()


        token = self.get_verification_token()


        payload = {
            "ExistingSites": [],
            "IfModifiedSince": (
                "2000-01-01T00:00:00.000Z"
            )
        }


        headers = {
            "Accept": (
                "application/json, "
                "text/javascript, */*; q=0.01"
            ),

            "Content-Type": (
                "application/json; charset=UTF-8"
            ),

            "X-Requested-With": (
                "XMLHttpRequest"
            ),

            "RequestVerificationToken": token,

            "Referer": SITE_URL,

            "Origin": (
                "https://shamim.ssaa.ir"
            )
        }


        response = self.session.post(
            GET_MODIFIED_SITES_URL,
            json=payload,
            headers=headers,
            timeout=TIMEOUT
        )

        response.raise_for_status()

        return response.json()


    # --------------------------------------------------
    # Get requested station statuses
    # --------------------------------------------------

    def get_status(
        self,
        station_codes
    ):
        """
        Return health status for requested stations.
        """

        data = self.get_all_stations()


        station_index = {
            str(
                item["SiteCode"]
            ).strip().upper(): item
            for item in data
        }


        result = {}


        for code in station_codes:

            code = str(
                code
            ).strip().upper()


            station = station_index.get(
                code
            )


            if station is None:

                result[code] = "NOT_FOUND"
                continue


            result[code] = self.STATUS_MAP.get(
                station.get("Status"),
                "UNKNOWN"
            )


        return result




if __name__ == "__main__":
    username = '4711281207'
    password = 'mmd471'

    try:
        client = ShamimClient(username, password)

        # Fetch the complete response without filtering any fields.
        data = client.get_all_stations()

        output = json.dumps(
            data,
            ensure_ascii=False,
            indent=4
        )

        print(output)

        # Save the complete response as UTF-8 JSON.
        with open("shamim_response.json", "w", encoding="utf-8") as file:
            file.write(output)

        print("\nSaved to shamim_response.json")

    except Exception as error:
        print(f"Error: {error}")
    