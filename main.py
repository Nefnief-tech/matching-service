import os
import json
import logging
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.services.storage import Storage
from appwrite.query import Query
from typing import List, Dict, Any
import math

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Mock context for testing
class MockContext:
    class Req:
        def __init__(self, body=None, headers=None):
            self.body = body
            self.headers = headers or {}

    class Res:
        def __init__(self):
            self.status_code = 200
            self.response_data = None

        def json(self, data, status_code=200):
            self.status_code = status_code
            self.response_data = data
            print(f"Response ({status_code}): {json.dumps(data, indent=2)}")
            return data

    def __init__(self, body=None):
        self.req = self.Req(body=body)
        self.res = self.Res()

    def log(self, message):
        logger.info(f"Context log: {message}")

def main(context):
    """
    Appwrite function to stage compatible dating profiles for a user.
    Returns a list of profiles sorted by compatibility score.
    """
    logger.info("Matching stager function started")

    # Get request data
    data = json.loads(context.req.body or '{}')
    user_id = data.get('userId')
    processed_profile_ids = data.get('processedProfileIds', [])
    limit = min(int(data.get('limit', 10)), 50)  # Max 50 profiles

    logger.info(f"Request data - userId: {user_id}, processedProfileIds count: {len(processed_profile_ids)}, limit: {limit}")

    if not user_id:
        logger.error("userId is required but not provided")
        return context.res.json({
            'success': False,
            'error': 'userId is required'
        }, 400)

    try:
        # Initialize Appwrite client
        client = Client()
        client.set_endpoint(os.environ.get('APPWRITE_ENDPOINT', 'https://fra.cloud.appwrite.io/v1'))
        client.set_project(os.environ.get('APPWRITE_FUNCTION_PROJECT_ID', '6899062700398ffeae4f'))
        client.set_key(context.req.headers.get('x-appwrite-key', 'standard_2e487d6a80782363f879cf50ab4b0c2711c4e8d39fb989671e88275696846832b590a5dfe6206da193fa114e69c1888eaa108d4566f161f6dcf67372ffcd90e38d604c4d7844a8163684da6b282e08d8b3130cba66c5802e34c720c80b2dcab095a5e6c6a56ab468240f5c3965915260dd9bcf274b62feb27c8520ae33ac122d'))
        databases = Databases(client)
        logger.info("Appwrite client initialized")

        preferences = get_user_preferences(databases, user_id)
        if not preferences:
            logger.error(f"User preferences not found for userId: {user_id}")
            return context.res.json({
                'success': False,
                'error': 'User preferences not found'
            }, 404)

        logger.info(f"Retrieved user preferences: minAge={preferences.get('minAge')}, maxAge={preferences.get('maxAge')}, preferredGenders={preferences.get('preferredGenders')}")

        # Get user's own profile to exclude from results
        user_profile = get_user_profile(databases, user_id)
        if not user_profile:
            logger.error(f"User profile not found for userId: {user_id}")
            return context.res.json({
                'success': False,
                'error': 'User profile not found'
            }, 404)

        logger.info(f"Retrieved user profile: age={user_profile.get('age')}, gender={user_profile.get('gender')}, heightCm={user_profile.get('heightCm')}")

        # Query compatible profiles
        logger.info(f"Querying compatible profiles with limit: {limit * 2}")
        compatible_profiles = query_compatible_profiles(
            databases,
            preferences,
            user_profile,
            processed_profile_ids,
            limit * 2  # Get more to allow for filtering
        )

        logger.info(f"Found {len(compatible_profiles)} compatible profiles")

        # Calculate compatibility scores and sort
        logger.info("Calculating compatibility scores for profiles")
        scored_profiles = []
        for profile in compatible_profiles:
            score = calculate_compatibility_score(profile, user_profile, preferences)
            scored_profiles.append({
                'profile': profile,
                'compatibilityScore': score
            })
            logger.debug(f"Profile {profile.get('$id', 'unknown')}: compatibility score = {score:.3f}")

        # Sort by score descending and take top results
        scored_profiles.sort(key=lambda x: x['compatibilityScore'], reverse=True)
        top_profiles = scored_profiles[:limit]

        logger.info(f"Returning top {len(top_profiles)} profiles with highest compatibility scores")
        for i, profile_data in enumerate(top_profiles[:3]):  # Log top 3 scores
            logger.info(f"Top {i+1} profile score: {profile_data['compatibilityScore']:.3f}")

        return context.res.json({
            'success': True,
            'profiles': top_profiles
        })

    except Exception as e:
        logger.error(f"Error in matching stager: {str(e)}", exc_info=True)
        return context.res.json({
            'success': False,
            'error': str(e)
        }, 500)

def get_user_preferences(databases: Databases, user_id: str) -> Dict[str, Any]:
    """Get user's dating preferences."""
    try:
        logger.debug(f"Querying user preferences for userId: {user_id}")
        result = databases.list_documents(
            database_id=os.environ.get('APPWRITE_DATABASE_ID', 'threed-dating-db'),
            collection_id='user-preferences',
            queries=[
                Query.equal('userId', user_id)
            ]
        )
        if result['documents']:
            logger.debug(f"Found user preferences document")
            return result['documents'][0]
        else:
            logger.warning(f"No user preferences found for userId: {user_id}")
            return None
    except Exception as e:
        logger.error(f"Error getting user preferences for userId {user_id}: {str(e)}")
        return None

def get_user_profile(databases: Databases, user_id: str) -> Dict[str, Any]:
    """Get user's dating profile."""
    try:
        logger.debug(f"Querying user profile for userId: {user_id}")
        result = databases.list_documents(
            database_id=os.environ.get('APPWRITE_DATABASE_ID', 'threed-dating-db'),
            collection_id='dating-profiles',
            queries=[
                Query.equal('userId', user_id),
                Query.equal('isActive', True)
            ]
        )
        if result['documents']:
            logger.debug(f"Found active user profile")
            return result['documents'][0]
        else:
            logger.warning(f"No active user profile found for userId: {user_id}")
            return None
    except Exception as e:
        logger.error(f"Error getting user profile for userId {user_id}: {str(e)}")
        return None

def query_compatible_profiles(databases: Databases, preferences: Dict, user_profile: Dict,
                            processed_profile_ids: List[str], limit: int) -> List[Dict]:
    """Query profiles that match basic preference filters."""
    queries = [
        Query.equal('isActive', True),
        Query.not_equal('userId', user_profile['userId']),  # Exclude own profile
    ]

    # Add preference filters
    if preferences.get('minAge'):
        queries.append(Query.greater_than_equal('age', preferences['minAge']))
        logger.debug(f"Added minAge filter: >= {preferences['minAge']}")
    if preferences.get('maxAge'):
        queries.append(Query.less_than_equal('age', preferences['maxAge']))
        logger.debug(f"Added maxAge filter: <= {preferences['maxAge']}")
    if preferences.get('minHeightCm'):
        queries.append(Query.greater_than_equal('heightCm', preferences['minHeightCm']))
        logger.debug(f"Added minHeightCm filter: >= {preferences['minHeightCm']}")
    if preferences.get('maxHeightCm'):
        queries.append(Query.less_than_equal('heightCm', preferences['maxHeightCm']))
        logger.debug(f"Added maxHeightCm filter: <= {preferences['maxHeightCm']}")

    # Gender preferences
    if preferences.get('preferredGenders'):
        gender_queries = []
        for gender in preferences['preferredGenders']:
            gender_queries.append(Query.equal('gender', gender))
        if gender_queries:
            queries.append(Query.or_(gender_queries))
            logger.debug(f"Added gender preferences filter: {preferences['preferredGenders']}")

    # Exclude already processed profiles
    if processed_profile_ids:
        for profile_id in processed_profile_ids:
            queries.append(Query.not_equal('$id', profile_id))
        logger.debug(f"Excluding {len(processed_profile_ids)} already processed profiles")

    logger.debug(f"Final query has {len(queries)} conditions, requesting limit: {limit}")

    try:
        result = databases.list_documents(
            database_id=os.environ.get('APPWRITE_DATABASE_ID', 'threed-dating-db'),
            collection_id='dating-profiles',
            queries=queries,
            limit=limit
        )
        profiles = result['documents']
        logger.info(f"Query returned {len(profiles)} profiles")
        return profiles
    except Exception as e:
        logger.error(f"Error querying profiles: {str(e)}")
        return []

def calculate_compatibility_score(profile: Dict, user_profile: Dict, preferences: Dict) -> float:
    """Calculate compatibility score between two profiles."""
    score = 0.0
    total_weight = 0.0

    profile_id = profile.get('$id', 'unknown')
    logger.debug(f"Calculating compatibility score for profile {profile_id}")

    # Age compatibility (weight: 0.3)
    age_diff = abs(profile['age'] - user_profile['age'])
    age_score = max(0, 1 - (age_diff / 20))  # Perfect match if same age, 0 if 20+ years diff
    score += age_score * 0.3
    total_weight += 0.3
    logger.debug(f"  Age score: {age_score:.3f} (diff: {age_diff} years)")

    # Height compatibility (weight: 0.2)
    height_diff = abs(profile['heightCm'] - user_profile['heightCm'])
    height_score = max(0, 1 - (height_diff / 30))  # Perfect match if same height, 0 if 30+ cm diff
    score += height_score * 0.2
    total_weight += 0.2
    logger.debug(f"  Height score: {height_score:.3f} (diff: {height_diff} cm)")

    # Hair color preference (weight: 0.15)
    if preferences.get('preferredHairColors') and profile.get('hairColor'):
        if profile['hairColor'] in preferences['preferredHairColors']:
            hair_score = 1.0
            logger.debug(f"  Hair color match: {profile['hairColor']} in preferred colors")
        else:
            hair_score = 0.3  # Partial score for non-preferred but not zero
            logger.debug(f"  Hair color partial match: {profile['hairColor']} not in preferred colors")
    else:
        hair_score = 0.5  # Neutral score when no preference
        logger.debug(f"  Hair color neutral: no preference specified")
    score += hair_score * 0.15
    total_weight += 0.15

    # Sports preferences (weight: 0.2)
    sports_score = calculate_sports_compatibility(
        profile.get('sportsPreferences', []),
        user_profile.get('sportsPreferences', []),
        preferences.get('preferredSports', [])
    )
    score += sports_score * 0.2
    total_weight += 0.2
    logger.debug(f"  Sports score: {sports_score:.3f}")

    # Gender preference match (weight: 0.15)
    if preferences.get('preferredGenders') and profile.get('gender'):
        if profile['gender'] in preferences['preferredGenders']:
            gender_score = 1.0
            logger.debug(f"  Gender match: {profile['gender']} in preferred genders")
        else:
            gender_score = 0.0  # No match if gender not preferred
            logger.debug(f"  Gender no match: {profile['gender']} not in preferred genders")
    else:
        gender_score = 0.8  # High score when no gender preference specified
        logger.debug(f"  Gender neutral: no preference specified")
    score += gender_score * 0.15
    total_weight += 0.15

    final_score = score / total_weight if total_weight > 0 else 0.0
    logger.debug(f"  Final compatibility score: {final_score:.3f}")
    return final_score

def calculate_sports_compatibility(profile_sports: List[str], user_sports: List[str],
                                 preferred_sports: List[str]) -> float:
    """Calculate sports compatibility score."""
    logger.debug(f"  Calculating sports compatibility - profile_sports: {profile_sports}, user_sports: {user_sports}, preferred_sports: {preferred_sports}")

    if not profile_sports and not user_sports:
        logger.debug(f"  No sports for both profiles, returning neutral score: 0.8")
        return 0.8  # Neutral score for no sports preferences

    if not preferred_sports:
        # No specific sports preferences, score based on overlap with user's sports
        if not user_sports:
            logger.debug(f"  No user sports and no preferences, returning neutral score: 0.6")
            return 0.6  # Neutral when user has no sports
        overlap = len(set(profile_sports) & set(user_sports))
        score = min(1.0, overlap / len(user_sports))
        logger.debug(f"  Sports overlap with user sports: {overlap}/{len(user_sports)} = {score:.3f}")
        return score
    else:
        # Score based on match with preferred sports
        overlap = len(set(profile_sports) & set(preferred_sports))
        if overlap > 0:
            score = min(1.0, overlap / len(preferred_sports))
            logger.debug(f"  Sports overlap with preferred sports: {overlap}/{len(preferred_sports)} = {score:.3f}")
            return score
        else:
            # No overlap with preferred sports, but check if profile has any sports
            score = 0.2 if profile_sports else 0.1
            logger.debug(f"  No overlap with preferred sports, profile has sports: {bool(profile_sports)}, score: {score}")
            return score

    logger.debug(f"  Default sports score: 0.5")
    return 0.5  # Default neutral score


# Test function for local development
def test_matching_stager():
    """Test the matching stager function with mock data."""
    print("\n" + "="*60)
    print("TESTING MATCHING STAGER FUNCTION")
    print("="*60)

    # Create mock context with test data
    test_data = {
        'userId': 'test_user_123',
        'processedProfileIds': ['processed_profile_1', 'processed_profile_2'],
        'limit': 5
    }

    context = MockContext(body=json.dumps(test_data))

    # Set debug logging for testing
    logging.getLogger().setLevel(logging.DEBUG)

    print(f"Testing with data: {test_data}")
    print("-" * 40)

    try:
        result = main(context)
        print("-" * 40)
        print("Test completed successfully!")
        return result
    except Exception as e:
        print(f"-" * 40)
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    # Run test when script is executed directly
    test_matching_stager()