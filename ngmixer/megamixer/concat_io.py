#######################################################
# setup CONCAT dict
# each time you create a new CONCAT class, add it to this dict
CONCAT = {}

from .concat import Concat
CONCAT['concat'] = Concat

from .desconcat import DESConcat,DESMetacalConcat,DESAdmomMetacalConcat
CONCAT['concat-des'] = DESConcat
CONCAT['concat-metacal-des'] = DESMetacalConcat
CONCAT['concat-metacal-admom-des'] = DESAdmomMetacalConcat

from .forcephotconcat import FPConcat
CONCAT['concat-forcephot'] = FPConcat

def get_concat_class(concat_name):
    """
    returns the concat class for a given concat_name
    """
    cftype = concat_name.lower()
    assert cftype in CONCAT,'could not find concat class %s' % cftype
    return CONCAT[cftype]
